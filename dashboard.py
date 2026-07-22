from pathlib import Path
import io
import re
import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Dashboard SK KONTAKT BRNO", page_icon="🏊", layout="wide")

GUIDE_TEXT = """Tento dashboard slouží k analýze odpovědí dotazníku a k sestavení rozvrhu plaveckých lekcí.

Doporučený postup:
1. Nejprve si prohlédněte Souhrn.
2. Poté zkontrolujte Jistotu odpovědí.
3. Následně analyzujte poptávku po termínech.
4. Zkontrolujte problémové klienty.
5. Věnujte pozornost komentářům a specifickým požadavkům.
6. Nakonec využijte doporučení pro rozvrh."""

HELP_SUMMARY = """Tato stránka obsahuje základní statistiky dotazníku a rychlý přehled respondentů."""

HELP_CERTAINTY = """Definitivní:
Klient uvedl, že s danými termíny lze počítat.

Pravděpodobná:
Klient předpokládá účast, ale situace se může změnit.

Nejistá:
Klient zatím nezná školní, pracovní nebo jiné časové možnosti."""

HELP_WEIGHTED = """Vážená poptávka zohledňuje spolehlivost odpovědí.

Výpočet:

Definitivní odpověď = 1.0
Pravděpodobná odpověď = 0.5
Nejistá odpověď = 0.2

Vyšší vážená poptávka znamená vyšší pravděpodobnost skutečné účasti."""

HELP_DEMAND = """Tabulka zobrazuje zájem o jednotlivé lekce.

Každý termín musí být zobrazen jako:

Den | Čas | Bazén

Například:

Pondělí | 16:00–17:00 | Kraví hora

Stejný čas v různých dnech se nesmí slučovat."""

HELP_PROBLEMATIC = """Klienti, kteří mohou být obtížně zařaditelní do rozvrhu.

Patří sem zejména:
- klienti s jediným možným termínem,
- klienti požadující více lekcí než kolik uvedli možností,
- klienti s velmi omezenou časovou flexibilitou.

Tyto klienty zvýrazni červeně."""

HELP_FLEXIBLE = """Klienti s více možnými termíny.

Jsou nejvhodnější pro optimalizaci rozvrhu a případné přesuny mezi lekcemi.

Tyto klienty zvýrazni zeleně."""

HELP_COMMENTS = """Sekce obsahuje všechny odpovědi s komentářem nebo speciálním požadavkem.

Slouží k zachycení informací, které nejsou obsaženy v běžných odpovědích dotazníku.

Filtr:
- pouze komentáře
- pouze specifické požadavky
- obojí"""

HELP_RECOMMENDATIONS = """Termíny jsou seřazeny podle vážené poptávky.

Vyšší hodnota znamená vyšší prioritu při tvorbě rozvrhu.

Pro skupinové a individuální lekce zobrazovat doporučení odděleně."""

WEEKDAY_ORDER = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek"]
WEEKDAY_RANK = {day: index for index, day in enumerate(WEEKDAY_ORDER)}
WEEKDAY_SHORT = {
    "Pondělí": "PO",
    "Úterý": "ÚT",
    "Středa": "ST",
    "Čtvrtek": "ČT",
    "Pátek": "PÁ",
}


def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def parse_term_detail(value):
    text = normalize_text(value)
    if not text:
        return "", ""

    compact = " ".join(text.split())
    time_match = re.search(r"(\d{1,2}[:.]\d{2}\s*[–-]\s*\d{1,2}[:.]\d{2})", compact)

    if time_match:
        time_part = time_match.group(1)
        time_part = time_part.replace(".", ":")
        time_part = re.sub(r"\s*[–-]\s*", "–", time_part)
        pool_part = compact[time_match.end():].strip(" ,")
        return time_part, pool_part

    parts = compact.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return compact, ""


def parse_time_options(value, weekday=None):
    if pd.isna(value):
        return []

    text = normalize_text(value)
    if not text or text.lower() == "nechci":
        return []

    options = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "nechci" or part.lower().startswith("nechci"):
            continue
        time_part, pool_part = parse_term_detail(part)
        if not time_part:
            continue
        options.append({
            "Den": weekday or "Neuvedeno",
            "Čas": time_part,
            "Bazén": pool_part,
        })
    return options


def parse_lessons_count(value):
    text = normalize_text(value)
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def classify_certainty(value):
    text = normalize_text(value).lower()
    if "definitiv" in text:
        return "Definitivní"
    if "pravděpodob" in text or "pravdepodob" in text or "spíše" in text or "spise" in text:
        return "Pravděpodobné"
    if "nevím" in text or "nevim" in text:
        return "Nejisté"
    return "Nejisté"


def classify_satisfaction(value):
    text = " ".join(normalize_text(value).lower().split())
    if text.startswith("spíše ano") or text.startswith("spise ano"):
        return "Spíše ano"
    if text.startswith("spíše ne") or text.startswith("spise ne"):
        return "Spíše ne"
    if text.startswith("ano"):
        return "Ano"
    if text.startswith("ne"):
        return "Ne"
    # Fallback: nezařazené nebo prázdné odpovědi počítáme jako "Ne",
    # aby rozpad vždy pokryl všechny respondenty.
    return "Ne"


def certainty_weight(certainty):
    weights = {
        "Definitivní": 1.0,
        "Pravděpodobné": 0.5,
        "Nejisté": 0.2,
    }
    return weights.get(certainty, 0.2)


def map_lesson_type(value):
    text = normalize_text(value).lower()
    if "skupin" in text:
        return "Skupinové"
    if "indiv" in text:
        return "Individuální"
    return "Neuvedeno"


def has_specific_request(comment_text):
    text = normalize_text(comment_text).lower()
    if not text:
        return False
    request_pattern = (
        r"pros[ií]m|po[žz]ad|prefer|chci|cht[ěe]l|pot[řr]ebuji|"
        r"zachovat|term[ií]n|[čc]as|pond[ěe]l|[úu]ter|st[řr]ed|[čc]tvr|p[áa]tek|"
        r"koci|krav[ií]|lu[žz][áa]n|pon[áa]v"
    )
    return bool(re.search(request_pattern, text))


def is_problematic_client(row):
    requested_lessons = row.get("requested_lessons")
    selected_terms = int(row.get("selected_terms_count", 0))
    certainty = normalize_text(row.get("active_certainty"))

    wants_more_than_selected = pd.notna(requested_lessons) and int(requested_lessons) > selected_terms
    only_one_term = selected_terms == 1
    definitive_and_less_than_two = certainty == "Definitivní" and selected_terms < 2

    return wants_more_than_selected or only_one_term or definitive_and_less_than_two


def get_preference_subset(dataframe, keyword):
    type_col = next((col for col in dataframe.columns if "typ lekce" in col.lower()), None)
    if not type_col:
        return dataframe.copy()
    return dataframe[dataframe[type_col].astype(str).str.lower().str.contains(keyword, na=False)].copy()


def get_notes_path():
    return Path(__file__).resolve().parent / "poznamky_organizatora.csv"


def load_organizer_notes():
    path = get_notes_path()
    if not path.exists():
        return pd.DataFrame(columns=["response_id", "Poznámka organizátora"])

    notes_df = pd.read_csv(path, encoding="utf-8-sig", dtype={"response_id": str})
    if "response_id" not in notes_df.columns:
        notes_df["response_id"] = ""
    if "Poznámka organizátora" not in notes_df.columns:
        notes_df["Poznámka organizátora"] = ""

    notes_df["response_id"] = notes_df["response_id"].astype(str)
    notes_df["Poznámka organizátora"] = notes_df["Poznámka organizátora"].fillna("").astype(str)
    return notes_df[["response_id", "Poznámka organizátora"]]


def save_organizer_note(response_id, note_text):
    notes_df = load_organizer_notes()
    row_id = str(response_id)

    notes_df = notes_df[notes_df["response_id"] != row_id]
    cleaned_note = normalize_text(note_text)
    if cleaned_note:
        notes_df = pd.concat(
            [
                notes_df,
                pd.DataFrame([
                    {
                        "response_id": row_id,
                        "Poznámka organizátora": cleaned_note,
                    }
                ]),
            ],
            ignore_index=True,
        )

    notes_df.to_csv(get_notes_path(), index=False, encoding="utf-8-sig")


def page_header_with_help(title, help_text, key):
    title_col, help_col = st.columns([0.9, 0.1])
    with title_col:
        st.subheader(title)
    with help_col:
        with st.expander("ℹ️"):
            st.markdown(help_text)


def time_to_minutes(time_text):
    text = normalize_text(time_text)
    match = re.search(r"(\d{1,2})[:.](\d{2})", text)
    if not match:
        return 10_000
    hours = int(match.group(1))
    minutes = int(match.group(2))
    return hours * 60 + minutes


def sort_by_day_and_time(dataframe):
    if dataframe.empty or "Den" not in dataframe.columns or "Čas" not in dataframe.columns:
        return dataframe

    df_sorted = dataframe.copy()
    df_sorted["_day_rank"] = df_sorted["Den"].map(WEEKDAY_RANK).fillna(len(WEEKDAY_ORDER))
    df_sorted["_time_rank"] = df_sorted["Čas"].apply(time_to_minutes)
    df_sorted = df_sorted.sort_values(["_day_rank", "_time_rank", "Bazén"], ascending=[True, True, True])
    return df_sorted.drop(columns=["_day_rank", "_time_rank"])


def format_term_dict(term_dict):
    day = normalize_text(term_dict.get("Den"))
    time = normalize_text(term_dict.get("Čas"))
    pool = normalize_text(term_dict.get("Bazén"))
    day_short = WEEKDAY_SHORT.get(day, day)
    return " ".join(part for part in [day_short, time, pool] if part).strip()


def format_cell_value(value):
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            if isinstance(item, dict) and {"Den", "Čas", "Bazén"}.issubset(item.keys()):
                parts.append(format_term_dict(item))
            elif isinstance(item, dict):
                parts.append(", ".join(f"{k}: {normalize_text(v)}" for k, v in item.items()))
            else:
                parts.append(normalize_text(item))
        return "\n".join([part for part in parts if part])

    if isinstance(value, dict):
        if {"Den", "Čas", "Bazén"}.issubset(value.keys()):
            return format_term_dict(value)
        return ", ".join(f"{k}: {normalize_text(v)}" for k, v in value.items())

    return value


def prepare_dataframe_for_display(dataframe):
    display_df = dataframe.copy()
    for col in display_df.columns:
        display_df[col] = display_df[col].apply(format_cell_value)
    return display_df


def render_dataframe(dataframe, **kwargs):
    st.dataframe(prepare_dataframe_for_display(dataframe), **kwargs)


@st.cache_data(show_spinner=False)
def load_data(csv_bytes):
    df = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig")
    df["response_id"] = df.index.astype(str)

    weekday_names = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek"]
    group_cols = [col for col in df.columns if any(day in col for day in weekday_names) and "SK" in col]
    ind_cols = [col for col in df.columns if any(day in col for day in weekday_names) and "IND" in col]

    def extract_terms(row, columns):
        terms = []
        for col in columns:
            weekday = next((day for day in weekday_names if day in col), None)
            terms.extend(parse_time_options(row[col], weekday=weekday))
        return terms

    df = df.copy()
    df["group_terms"] = df.apply(lambda row: extract_terms(row, group_cols), axis=1)
    df["individual_terms"] = df.apply(lambda row: extract_terms(row, ind_cols), axis=1)

    certainty_cols = [col for col in df.columns if "Jak jisté jsou Vaše odpovědi ohledně časů?" in col]
    group_cert_col = next((col for col in certainty_cols if not col.endswith(".1")), None)
    individual_cert_col = next((col for col in certainty_cols if col.endswith(".1")), None)

    type_col = next((col for col in df.columns if "typ lekce" in col.lower()), None)
    lessons_col = next((col for col in df.columns if "lekcí týdně" in col.lower()), None)
    comment_col = next((col for col in df.columns if "koment" in col.lower() or "požad" in col.lower()), None)
    surname_col = next((col for col in df.columns if "příjmení" in col.lower()), None)
    name_col = next((col for col in df.columns if "jméno" in col.lower()), None)

    df["certainty_group"] = df[group_cert_col].apply(classify_certainty) if group_cert_col else "Nejisté"
    df["certainty_individual"] = df[individual_cert_col].apply(classify_certainty) if individual_cert_col else "Nejisté"

    df["lesson_type"] = df[type_col].apply(map_lesson_type) if type_col else "Neuvedeno"
    df["requested_lessons"] = df[lessons_col].apply(parse_lessons_count) if lessons_col else None
    df["comment"] = df[comment_col].apply(normalize_text) if comment_col else ""
    df["has_comment"] = df["comment"].str.len() > 0
    df["has_specific_request"] = df["comment"].apply(has_specific_request)
    df["Jméno"] = df[name_col].apply(normalize_text) if name_col else ""
    df["Příjmení"] = df[surname_col].apply(normalize_text) if surname_col else ""

    if surname_col and name_col:
        df["Klient"] = (
            df[surname_col].apply(normalize_text)
            + " "
            + df[name_col].apply(normalize_text)
        ).str.strip()
    elif surname_col:
        df["Klient"] = df[surname_col].apply(normalize_text)
    elif name_col:
        df["Klient"] = df[name_col].apply(normalize_text)
    else:
        df["Klient"] = ""

    df["active_terms"] = df.apply(
        lambda row: row["group_terms"] if row["lesson_type"] == "Skupinové"
        else row["individual_terms"] if row["lesson_type"] == "Individuální"
        else [],
        axis=1,
    )
    df["selected_terms_count"] = df["active_terms"].apply(len)
    df["active_certainty"] = df.apply(
        lambda row: row["certainty_group"] if row["lesson_type"] == "Skupinové"
        else row["certainty_individual"] if row["lesson_type"] == "Individuální"
        else "Nejisté",
        axis=1,
    )
    df["is_problematic"] = df.apply(is_problematic_client, axis=1)
    df["is_flexible"] = df["selected_terms_count"] >= 4
    return df


st.title("Dashboard SK KONTAKT BRNO")
st.caption("Základní přehled odpovědí z dotazníku")

with st.expander("📖 Jak pracovat s dashboardem"):
    st.markdown(GUIDE_TEXT)

uploaded_file = st.file_uploader("Nahraj CSV soubor", type=["csv"])

if uploaded_file is None:
    st.info(
        "Nahrajte CSV soubor s odpověďmi dotazníku.\n\n"
        "Instrukce:\n"
        "1. Klikněte na tlačítko pro nahrání souboru.\n"
        "2. Vyberte export odpovědí z Google Forms ve formátu CSV.\n"
        "3. Po nahrání se automaticky provede kompletní analýza."
    )
    st.stop()

csv_bytes = uploaded_file.getvalue()
if not csv_bytes:
    st.error("Nahraný soubor je prázdný.")
    st.stop()

df = load_data(csv_bytes)
notes_df = load_organizer_notes()
notes_map = dict(zip(notes_df["response_id"], notes_df["Poznámka organizátora"]))
df["Poznámka organizátora"] = df["response_id"].map(notes_map).fillna("")
st.success(f"Nahrán soubor: {uploaded_file.name}")

if df.empty:
    st.error("Nahraný CSV soubor neobsahuje žádná data.")
    st.stop()

tabs = st.tabs([
    "Souhrn",
    "Skupinové lekce",
    "Individuální lekce",
    "Jistota odpovědí",
    "Poptávka po termínech",
    "Klienti",
    "Komentáře a požadavky",
    "Rizikoví klienti",
    "Doporučení pro rozvrh",
])

with tabs[0]:
    page_header_with_help("Souhrn", HELP_SUMMARY, "help_summary")

    total_responses = len(df)
    type_col = next((col for col in df.columns if "typ lekce" in col.lower()), None)
    group_count = int(df[type_col].astype(str).str.lower().str.contains("skupin", na=False).sum()) if type_col else 0
    individual_count = int(df[type_col].astype(str).str.lower().str.contains("indiv", na=False).sum()) if type_col else 0

    satisfaction_col = next((col for col in df.columns if "Vyhovuje Vám aktuální rozvrh?" in col), None)
    if satisfaction_col:
        satisfaction_labels = df[satisfaction_col].apply(classify_satisfaction)
    else:
        satisfaction_labels = pd.Series(["Ne"] * total_responses)

    satisfaction_order = ["Ano", "Spíše ano", "Spíše ne", "Ne"]
    satisfaction_counts = {
        label: int((satisfaction_labels == label).sum())
        for label in satisfaction_order
    }
    satisfied_count = satisfaction_counts["Ano"] + satisfaction_counts["Spíše ano"]
    satisfaction_total = sum(satisfaction_counts.values())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Počet odpovědí", total_responses)
    col2.metric("Počet skupinových plavců", group_count)
    col3.metric("Počet individuálních plavců", individual_count)
    col4.metric("Spokojení (Ano + Spíše ano)", satisfied_count)
    col4.caption(f"Ano: {satisfaction_counts['Ano']} | Spíše ano: {satisfaction_counts['Spíše ano']}")

    schedule_df = pd.DataFrame(
        {
            "Odpověď": satisfaction_order,
            "Počet": [satisfaction_counts[label] for label in satisfaction_order],
        }
    )
    schedule_df["Procento"] = ((schedule_df["Počet"] / total_responses) * 100).round(1)

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.markdown("#### Sloupcový graf odpovědí")
        bar_chart = alt.Chart(schedule_df).mark_bar().encode(
            x=alt.X("Odpověď:N", sort=satisfaction_order, title="Odpověď"),
            y=alt.Y("Počet:Q", title="Počet respondentů"),
            color=alt.Color(
                "Odpověď:N",
                sort=satisfaction_order,
                legend=None,
                scale=alt.Scale(
                    domain=satisfaction_order,
                    range=["#2e7d32", "#66bb6a", "#f9a825", "#c62828"],
                ),
            ),
            tooltip=[
                alt.Tooltip("Odpověď:N", title="Odpověď"),
                alt.Tooltip("Počet:Q", title="Počet", format=".0f"),
                alt.Tooltip("Procento:Q", title="Procento", format=".1f"),
            ],
        )
        st.altair_chart(bar_chart, use_container_width=True)

    with chart_col2:
        st.markdown("#### Koláčový graf odpovědí")
        pie_chart = alt.Chart(schedule_df).mark_arc().encode(
            theta=alt.Theta("Počet:Q"),
            color=alt.Color(
                "Odpověď:N",
                sort=satisfaction_order,
                scale=alt.Scale(
                    domain=satisfaction_order,
                    range=["#2e7d32", "#66bb6a", "#f9a825", "#c62828"],
                ),
                legend=alt.Legend(title="Odpověď"),
            ),
            tooltip=[
                alt.Tooltip("Odpověď:N", title="Odpověď"),
                alt.Tooltip("Počet:Q", title="Počet", format=".0f"),
                alt.Tooltip("Procento:Q", title="Procento", format=".1f"),
            ],
        )
        st.altair_chart(pie_chart, use_container_width=True)

    st.markdown("#### Přehled odpovědí")
    schedule_table = schedule_df.copy()
    schedule_table["Procento"] = schedule_table["Procento"].map(lambda value: f"{value:.1f} %")
    render_dataframe(schedule_table, use_container_width=True)

    if satisfaction_total != total_responses:
        st.error(
            f"Kontrola součtu neprošla: součet odpovědí ({satisfaction_total}) neodpovídá počtu respondentů ({total_responses})."
        )

    render_dataframe(df[["group_terms", "individual_terms"]].head(), use_container_width=True)

def build_term_summary(df, term_col, certainty_col):
    rows = []
    for _, row in df.iterrows():
        for term in row[term_col]:
            if not term:
                continue

            day = normalize_text(term.get("Den")) or "Neuvedeno"
            time_part = normalize_text(term.get("Čas"))
            pool_part = normalize_text(term.get("Bazén"))
            certainty = classify_certainty(row.get(certainty_col, "Nejisté"))

            rows.append({
                "Den": day,
                "Čas": time_part,
                "Bazén": pool_part,
                "Počet zájemců": 1,
                "Vážená poptávka": certainty_weight(certainty),
                "Definitivní": 1 if certainty == "Definitivní" else 0,
                "Pravděpodobné": 1 if certainty == "Pravděpodobné" else 0,
                "Nejisté": 1 if certainty == "Nejisté" else 0,
            })

    if not rows:
        return pd.DataFrame(columns=["Den", "Čas", "Bazén", "Počet zájemců", "Vážená poptávka", "Definitivní", "Pravděpodobné", "Nejisté"])

    summary = pd.DataFrame(rows)
    summary = summary.groupby(["Den", "Čas", "Bazén"], as_index=False).agg(
        {
            "Počet zájemců": "sum",
            "Vážená poptávka": "sum",
            "Definitivní": "sum",
            "Pravděpodobné": "sum",
            "Nejisté": "sum",
        }
    )
    return sort_by_day_and_time(summary)


def render_heatmap(summary, color_scheme):
    if summary.empty:
        return

    day_order = [day for day in WEEKDAY_ORDER if day in summary["Den"].unique()]
    time_order = sorted(summary["Čas"].unique(), key=time_to_minutes)

    heatmap_source = (
        summary.groupby(["Den", "Čas"], as_index=False)
        .agg(
            {
                "Počet zájemců": "sum",
                "Vážená poptávka": "sum",
                "Definitivní": "sum",
                "Pravděpodobné": "sum",
                "Nejisté": "sum",
            }
        )
        .copy()
    )
    heatmap_source["Vážená poptávka (1 desetinné místo)"] = heatmap_source["Vážená poptávka"].round(1)

    base = alt.Chart(heatmap_source).encode(
        x=alt.X("Čas:N", sort=time_order, title="Čas"),
        y=alt.Y("Den:N", sort=day_order, title="Den"),
    )

    rect = base.mark_rect().encode(
        color=alt.Color("Vážená poptávka:Q", title="Vážená poptávka", scale=alt.Scale(scheme=color_scheme)),
        tooltip=[
            alt.Tooltip("Den:N", title="Den"),
            alt.Tooltip("Čas:N", title="Čas"),
            alt.Tooltip("Počet zájemců:Q", title="Počet zájemců", format=".0f"),
            alt.Tooltip("Vážená poptávka:Q", title="Vážená poptávka", format=".1f"),
            alt.Tooltip("Definitivní:Q", title="Počet definitivních odpovědí", format=".0f"),
            alt.Tooltip("Pravděpodobné:Q", title="Počet pravděpodobných odpovědí", format=".0f"),
            alt.Tooltip("Nejisté:Q", title="Počet nejistých odpovědí", format=".0f"),
        ],
    )

    text = base.mark_text(size=12).encode(
        text=alt.Text("Vážená poptávka:Q", format=".1f"),
        color=alt.value("black"),
    )

    st.altair_chart((rect + text).properties(height=280), use_container_width=True)


with tabs[1]:
    page_header_with_help("Skupinové lekce", HELP_WEIGHTED, "help_group")
    group_data = get_preference_subset(df, "skupin")
    if group_data.empty:
        st.info("Žádná data pro skupinové lekce.")
    else:
        group_summary = build_term_summary(group_data, "group_terms", "certainty_group")
        render_dataframe(group_summary, use_container_width=True)

        if not group_summary.empty:
            st.subheader("Heatmapa – vážená poptávka")
            render_heatmap(group_summary, "blues")

with tabs[2]:
    page_header_with_help("Individuální lekce", HELP_WEIGHTED, "help_individual")
    individual_data = get_preference_subset(df, "indiv")
    if individual_data.empty:
        st.info("Žádná data pro individuální lekce.")
    else:
        individual_summary = build_term_summary(individual_data, "individual_terms", "certainty_individual")
        render_dataframe(individual_summary, use_container_width=True)

        if not individual_summary.empty:
            st.subheader("Heatmapa – vážená poptávka")
            render_heatmap(individual_summary, "oranges")

with tabs[3]:
    page_header_with_help("Jistota odpovědí", HELP_CERTAINTY, "help_certainty")

    certainty_overview = pd.DataFrame(
        {
            "Kategorie": ["Definitivní", "Pravděpodobné", "Nejisté"],
            "Skupinové": [
                int((df["certainty_group"] == "Definitivní").sum()),
                int((df["certainty_group"] == "Pravděpodobné").sum()),
                int((df["certainty_group"] == "Nejisté").sum()),
            ],
            "Individuální": [
                int((df["certainty_individual"] == "Definitivní").sum()),
                int((df["certainty_individual"] == "Pravděpodobné").sum()),
                int((df["certainty_individual"] == "Nejisté").sum()),
            ],
        }
    )
    render_dataframe(certainty_overview, use_container_width=True)

with tabs[4]:
    page_header_with_help("Poptávka po termínech", f"{HELP_DEMAND}\n\n{HELP_WEIGHTED}", "help_demand")

    st.markdown("#### Skupinové lekce")
    group_data = get_preference_subset(df, "skupin")
    group_demand = build_term_summary(group_data, "group_terms", "certainty_group")
    if group_demand.empty:
        st.info("Žádná data pro skupinové lekce.")
    else:
        render_dataframe(group_demand, use_container_width=True)

    st.markdown("#### Individuální lekce")
    individual_data = get_preference_subset(df, "indiv")
    individual_demand = build_term_summary(individual_data, "individual_terms", "certainty_individual")
    if individual_demand.empty:
        st.info("Žádná data pro individuální lekce.")
    else:
        render_dataframe(individual_demand, use_container_width=True)

with tabs[5]:
    page_header_with_help("Klienti", f"{HELP_PROBLEMATIC}\n\n{HELP_FLEXIBLE}", "help_clients")

    col1, col2, col3 = st.columns(3)
    with col1:
        lesson_type_filter = st.selectbox("Typ lekce", ["Vše", "Skupinové", "Individuální"])
        certainty_filter = st.multiselect(
            "Jistota odpovědi",
            ["Definitivní", "Pravděpodobná", "Nejistá"],
            default=["Definitivní", "Pravděpodobná", "Nejistá"],
        )

    with col2:
        lesson_options = sorted([int(x) for x in df["requested_lessons"].dropna().unique()])
        lessons_filter = st.multiselect(
            "Počet požadovaných lekcí týdně",
            lesson_options,
            default=lesson_options,
        )
        comment_filter = st.selectbox("Má komentář", ["Vše", "Ano", "Ne"])

    with col3:
        request_filter = st.selectbox("Má specifický požadavek", ["Vše", "Ano", "Ne"])
        problematic_only = st.checkbox("Pouze problémoví klienti", value=False)
        flexible_only = st.checkbox("Pouze flexibilní klienti", value=False)

    filtered = df.copy()

    if lesson_type_filter != "Vše":
        filtered = filtered[filtered["lesson_type"] == lesson_type_filter]

    certainty_map = {
        "Definitivní": "Definitivní",
        "Pravděpodobná": "Pravděpodobné",
        "Nejistá": "Nejisté",
    }
    selected_certainties = [certainty_map[item] for item in certainty_filter]
    filtered = filtered[filtered["active_certainty"].isin(selected_certainties)]

    if lessons_filter:
        filtered = filtered[filtered["requested_lessons"].isin(lessons_filter)]
    else:
        filtered = filtered.iloc[0:0]

    if comment_filter == "Ano":
        filtered = filtered[filtered["has_comment"]]
    elif comment_filter == "Ne":
        filtered = filtered[~filtered["has_comment"]]

    if request_filter == "Ano":
        filtered = filtered[filtered["has_specific_request"]]
    elif request_filter == "Ne":
        filtered = filtered[~filtered["has_specific_request"]]

    if problematic_only:
        filtered = filtered[filtered["is_problematic"]]

    if flexible_only:
        filtered = filtered[filtered["is_flexible"]]

    st.markdown("#### Poznámka organizátora")
    st.caption("Příklady: zavolat, ověřit rozvrh v září, nabídnout individuál, nabídnout skupinu")

    if filtered.empty:
        st.info("Pro aktuální filtry není k dispozici žádný klient pro zápis poznámky.")
    else:
        note_targets = filtered[["response_id", "Klient", "lesson_type", "Poznámka organizátora"]].copy()
        note_targets["label"] = note_targets.apply(
            lambda row: f"{row['Klient']} | {row['lesson_type']} | ID {row['response_id']}",
            axis=1,
        )
        selected_label = st.selectbox("Vyber klienta", note_targets["label"].tolist())
        selected_row = note_targets[note_targets["label"] == selected_label].iloc[0]
        selected_response_id = selected_row["response_id"]

        note_value = st.text_area(
            "Poznámka organizátora",
            value=selected_row["Poznámka organizátora"],
            key=f"organizer_note_{selected_response_id}",
            height=100,
        )

        if st.button("Uložit poznámku", key=f"save_note_{selected_response_id}"):
            save_organizer_note(selected_response_id, note_value)
            st.success("Poznámka byla uložena.")
            st.rerun()

    st.markdown("#### Souhrn filtrování")
    summary = (
        filtered.groupby(["lesson_type", "active_certainty"], dropna=False)
        .size()
        .reset_index(name="Počet klientů")
        .rename(columns={"lesson_type": "Typ lekce", "active_certainty": "Jistota odpovědi"})
        .sort_values(["Počet klientů", "Typ lekce"], ascending=[False, True])
    )
    render_dataframe(summary, use_container_width=True)

    st.markdown("#### Seznam klientů")

    def highlight_problematic_rows(row):
        if bool(row.get("Problémový klient", False)):
            return ["background-color: #ffe5e5; color: #b00020; font-weight: 600;"] * len(row)
        if bool(row.get("Flexibilní klient", False)):
            return ["background-color: #e7f7ea; color: #1b5e20; font-weight: 600;"] * len(row)
        return [""] * len(row)

    client_table = filtered[
        [
            "Klient",
            "lesson_type",
            "requested_lessons",
            "selected_terms_count",
            "active_certainty",
            "has_comment",
            "has_specific_request",
            "is_problematic",
            "is_flexible",
            "comment",
            "Poznámka organizátora",
        ]
    ].rename(
        columns={
            "lesson_type": "Typ lekce",
            "requested_lessons": "Počet požadovaných lekcí týdně",
            "selected_terms_count": "Počet vybraných termínů",
            "active_certainty": "Jistota odpovědi",
            "has_comment": "Má komentář",
            "has_specific_request": "Má specifický požadavek",
            "is_problematic": "Problémový klient",
            "is_flexible": "Flexibilní klient",
            "comment": "Komentář / specifický požadavek",
        }
    )
    display_client_table = prepare_dataframe_for_display(client_table)
    st.dataframe(display_client_table.style.apply(highlight_problematic_rows, axis=1), use_container_width=True)

with tabs[6]:
    page_header_with_help("Komentáře a požadavky", HELP_COMMENTS, "help_comments")

    comment_type_filter = st.selectbox(
        "Typ záznamu",
        ["obojí", "pouze komentáře", "pouze specifické požadavky"],
        index=0,
    )

    text_filter = st.text_input(
        "Filtrovat podle textu",
        placeholder="Např. Kociánka, úterý, individuální, Lužánky, Kraví hora",
    ).strip()

    comments_df = df[(df["has_comment"]) | (df["has_specific_request"])].copy()
    if comment_type_filter == "pouze komentáře":
        comments_df = comments_df[comments_df["has_comment"]]
    elif comment_type_filter == "pouze specifické požadavky":
        comments_df = comments_df[comments_df["has_specific_request"]]
    comments_df["term_text"] = comments_df["active_terms"].apply(
        lambda terms: " | ".join(
            f"{normalize_text(term.get('Den'))} {normalize_text(term.get('Čas'))} {normalize_text(term.get('Bazén'))}".strip()
            for term in terms
            if term
        )
    )
    comments_df["search_text"] = (
        comments_df["Jméno"].astype(str)
        + " "
        + comments_df["Příjmení"].astype(str)
        + " "
        + comments_df["lesson_type"].astype(str)
        + " "
        + comments_df["comment"].astype(str)
        + " "
        + comments_df["active_certainty"].astype(str)
        + " "
        + comments_df["term_text"].astype(str)
    ).str.lower()

    if text_filter:
        comments_df = comments_df[comments_df["search_text"].str.contains(text_filter.lower(), na=False)]

    comments_df = comments_df[
        [
            "Jméno",
            "Příjmení",
            "lesson_type",
            "comment",
            "active_certainty",
            "requested_lessons",
            "Poznámka organizátora",
        ]
    ].rename(
        columns={
            "lesson_type": "Typ lekce",
            "comment": "Komentář",
            "active_certainty": "Jistota odpovědi",
            "requested_lessons": "Požadovaný počet lekcí",
        }
    )

    if comments_df.empty:
        st.info("Žádné odpovědi s komentářem nebo specifickým požadavkem.")
    else:
        render_dataframe(comments_df, use_container_width=True)

with tabs[7]:
    page_header_with_help("Rizikoví klienti", HELP_PROBLEMATIC, "help_risky")

    risky_clients = df[
        (df["selected_terms_count"] == 1)
        & (df["active_certainty"] == "Definitivní")
        & (df["requested_lessons"].fillna(0) >= 2)
    ].copy()

    risky_clients["Míra problému"] = risky_clients["requested_lessons"].astype(int) - risky_clients["selected_terms_count"].astype(int)
    risky_clients = risky_clients.sort_values(
        ["Míra problému", "requested_lessons", "Klient"],
        ascending=[False, False, True],
    )

    risky_clients_view = risky_clients[
        [
            "Jméno",
            "Příjmení",
            "lesson_type",
            "requested_lessons",
            "selected_terms_count",
            "active_certainty",
            "Míra problému",
            "comment",
            "Poznámka organizátora",
        ]
    ].rename(
        columns={
            "lesson_type": "Typ lekce",
            "requested_lessons": "Požadovaný počet lekcí",
            "selected_terms_count": "Počet možných termínů",
            "active_certainty": "Jistota odpovědi",
            "comment": "Komentář",
        }
    )

    if risky_clients_view.empty:
        st.info("Žádní klienti nesplňují zadaná riziková kritéria.")
    else:
        render_dataframe(risky_clients_view, use_container_width=True)

with tabs[8]:
    page_header_with_help("Doporučení pro nový rozvrh", HELP_RECOMMENDATIONS, "help_recommendations")

    st.markdown("#### Skupinové lekce")
    group_recommendations = build_term_summary(get_preference_subset(df, "skupin"), "group_terms", "certainty_group")
    if group_recommendations.empty:
        st.info("Žádná data pro skupinové lekce.")
    else:
        render_dataframe(group_recommendations, use_container_width=True)

    st.markdown("#### Individuální lekce")
    individual_recommendations = build_term_summary(get_preference_subset(df, "indiv"), "individual_terms", "certainty_individual")
    if individual_recommendations.empty:
        st.info("Žádná data pro individuální lekce.")
    else:
        render_dataframe(individual_recommendations, use_container_width=True)
