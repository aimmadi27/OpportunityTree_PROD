import streamlit as st
import json
import pathlib
import tempfile
import pandas as pd
from dotenv import load_dotenv
from pdf2image import convert_from_path

from ocr_extractor import extract_page_json, merge_page_results
from llm_handler import LLMHandler
from auth import start_google_login, handle_oauth_callback, get_current_user, logout

st.set_page_config(
    page_title="Handwritten Form Extractor",
    page_icon="📝",
    layout="wide",
)
load_dotenv()

def init_state():
    st.session_state.pdf_pages = None
    st.session_state.last_pdf = None
    st.session_state.selected_pages = set()
    st.session_state.page_order = []
    st.session_state.page_schemas = {}
    st.session_state.pages_confirmed = False
    st.session_state.schemas_confirmed = False
    st.session_state.extraction_complete = False
    st.session_state.extracted_data = None

if "initialized" not in st.session_state:
    init_state()
    st.session_state.initialized = True

q = st.query_params
user = None

if "code" in q and "state" in q:
    user = handle_oauth_callback()

if not user:
    user = get_current_user()

if not user:
    st.title("Sign in to continue")
    st.caption("Use your Google account.")

    if "_auth_url" not in st.session_state:
        st.session_state["_auth_url"] = start_google_login()

    st.link_button("Continue with Google", st.session_state["_auth_url"], type="primary")
    st.stop()

with st.sidebar:
    if user.picture:
        st.image(user.picture, width=64)
    st.markdown(f"**{user.name}**")
    st.caption(user.email)

    st.divider()

    uploaded_pdf = st.file_uploader("📤 Upload filled PDF form", type=["pdf"])

    if st.button("Log out"):
        logout()
        st.rerun()

st.title("📝 Handwritten Form Extractor")
st.caption("Upload → select pages → assign schema → extract → review → export")

# -------------------------------------------------
# INIT LLM
# -------------------------------------------------
try:
    llm = LLMHandler()
except Exception as e:
    st.error(f"Failed to initialize LLM: {e}")
    st.stop()

# -------------------------------------------------
# LOAD SCHEMAS
# -------------------------------------------------
SCHEMA_DIR = "./schemas"
schema_files = sorted(pathlib.Path(SCHEMA_DIR).glob("*.json"))
if not schema_files:
    st.error("No schemas found in ./schemas/")
    st.stop()

schemas = {f.stem: json.load(open(f, "r", encoding="utf-8")) for f in schema_files}

# -------------------------------------------------
# HANDLE PDF UPLOAD
# -------------------------------------------------
if uploaded_pdf and uploaded_pdf.name != st.session_state.last_pdf:
    init_state()

    temp_pdf = pathlib.Path(f"./temp_{uploaded_pdf.name}")
    temp_pdf.write_bytes(uploaded_pdf.read())

    pages = convert_from_path(temp_pdf, dpi=150)
    st.session_state.pdf_pages = pages
    st.session_state.last_pdf = uploaded_pdf.name
    st.session_state.page_order = list(range(1, len(pages) + 1))
    st.session_state.selected_pages = set(st.session_state.page_order)

# -------------------------------------------------
# TABS
# -------------------------------------------------
tab_upload, tab_pages, tab_review, tab_export = st.tabs([
    "📤 Upload",
    "📄 Pages & Schema",
    "✏️ Review",
    "📥 Export",
])

# -------------------------------------------------
# TAB 1 — UPLOAD
# -------------------------------------------------
with tab_upload:
    if not uploaded_pdf:
        st.info("Upload a PDF to begin.")
    else:
        st.success(f"Loaded **{uploaded_pdf.name}** ({len(st.session_state.pdf_pages)} pages)")

# -------------------------------------------------
# TAB 2 — PAGE SELECTION + SCHEMA
# -------------------------------------------------
with tab_pages:
    if not st.session_state.pdf_pages:
        st.info("Upload a PDF first.")
        st.stop()

    st.subheader("📄 Select Pages")

    pages = st.session_state.pdf_pages
    new_selection = set()

    cols = st.columns(3)
    for idx, page_num in enumerate(st.session_state.page_order):
        with cols[idx % 3]:
            st.image(pages[page_num - 1], caption=f"Page {page_num}")
            if st.checkbox(
                f"Include Page {page_num}",
                value=(page_num in st.session_state.selected_pages),
                key=f"page_{page_num}",
                disabled=st.session_state.pages_confirmed,
            ):
                new_selection.add(page_num)

    if not st.session_state.pages_confirmed:
        if st.button("✅ Confirm Selected Pages", type="primary"):
            st.session_state.selected_pages = new_selection
            st.session_state.pages_confirmed = True
            st.rerun()
    else:
        st.success("Pages confirmed.")

    st.divider()
    if not st.session_state.pages_confirmed:
        st.stop()

    st.subheader("🧩 Assign Schema per Page")

    for page_num in sorted(st.session_state.selected_pages):
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(pages[page_num - 1], caption=f"Page {page_num}", width=180)
        with col2:
            st.session_state.page_schemas[page_num] = schemas[
                st.selectbox(
                    "Schema",
                    list(schemas.keys()),
                    key=f"schema_{page_num}",
                    disabled=st.session_state.schemas_confirmed,
                )
            ]

    if not st.session_state.schemas_confirmed:
        if st.button("✅ Confirm Schemas", type="primary"):
            st.session_state.schemas_confirmed = True
            st.rerun()
    else:
        st.success("Schemas confirmed.")

    st.divider()

    if st.session_state.schemas_confirmed and not st.session_state.extraction_complete:
        if st.button("🚀 Run Extraction", type="primary"):
            all_page_data = []
            progress = st.progress(0)
            status = st.empty()

            selected = sorted(st.session_state.selected_pages)

            for idx, page_num in enumerate(selected, start=1):
                status.write(f"Processing page {page_num}")
                page = pages[page_num - 1]
                schema = st.session_state.page_schemas[page_num]

                with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                    page.save(tmp.name, "PNG")
                    img_bytes = open(tmp.name, "rb").read()

                page_json = extract_page_json(
                    llm,
                    img_bytes,
                    page_num,
                    json.dumps(schema),
                )

                all_page_data.append(page_json)
                progress.progress(idx / len(selected))

            st.session_state.extracted_data = merge_page_results(all_page_data)
            st.session_state.extraction_complete = True
            st.success("Extraction complete.")

# -------------------------------------------------
# TAB 3 — REVIEW
# -------------------------------------------------
with tab_review:
    if not st.session_state.extraction_complete:
        st.info("Run extraction first.")
        st.stop()

    final_json = st.session_state.extracted_data
    edited_data = {}

    def pretty(k):
        return k.replace("_", " ").title()

    for section, fields in final_json.items():
        if not isinstance(fields, dict):
            continue

        filled = sum(1 for v in fields.values() if v)
        total = len(fields)

        with st.expander(f"{pretty(section)} ({filled}/{total})"):
            col1, col2 = st.columns(2)
            section_data = {}
            for i, (field, value) in enumerate(fields.items()):
                with col1 if i % 2 == 0 else col2:
                    section_data[field] = st.text_input(
                        pretty(field),
                        value=str(value) if value else "",
                        key=f"{section}_{field}",
                    )
            edited_data[section] = section_data

    st.session_state.extracted_data = edited_data

# -------------------------------------------------
# TAB 4 — EXPORT
# -------------------------------------------------
with tab_export:
    if not st.session_state.extraction_complete:
        st.info("Complete extraction first.")
        st.stop()

    st.subheader("📥 Export")

    if st.button("📊 Generate Excel", type="primary"):
        def flatten(d, parent=""):
            out = {}
            for k, v in d.items():
                nk = f"{parent}.{k}" if parent else k
                if isinstance(v, dict):
                    out.update(flatten(v, nk))
                else:
                    out[nk] = v
            return out

        flat = flatten(st.session_state.extracted_data)
        df = pd.DataFrame([flat])

        out_file = "import_ready.xlsx"
        df.to_excel(out_file, index=False)

        with open(out_file, "rb") as f:
            st.download_button("⬇️ Download Excel", f, file_name=out_file)

        st.success("Export ready.")
