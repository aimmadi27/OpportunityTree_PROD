import streamlit as st
import json
import pathlib
import tempfile
import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from pdf2image import convert_from_path

from ocr_extractor import extract_page_json, merge_page_results
from llm_handler import LLMHandler
from auth import start_google_login, handle_oauth_callback, get_current_user, logout

SCHEMA_DIR = "./schemas"

schemas = {}

for fname in os.listdir(SCHEMA_DIR):
    if fname.startswith("schema") and fname.endswith(".json"):
        num = int(fname.replace("schema", "").replace(".json", ""))
        with open(os.path.join(SCHEMA_DIR, fname)) as f:
            schemas[num] = json.load(f)

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

try:
    llm = LLMHandler()
except Exception as e:
    st.error(f"Failed to initialize LLM: {e}")
    st.stop()


if uploaded_pdf and uploaded_pdf.name != st.session_state.last_pdf:
    init_state()

    temp_pdf = pathlib.Path(f"./temp_{uploaded_pdf.name}")
    temp_pdf.write_bytes(uploaded_pdf.read())

    pages = convert_from_path(temp_pdf, dpi=150)
    st.session_state.pdf_pages = pages
    st.session_state.last_pdf = uploaded_pdf.name
    st.session_state.page_order = list(range(1, len(pages) + 1))
    st.session_state.selected_pages = set(st.session_state.page_order)

tab_upload, tab_pages, tab_review, tab_export = st.tabs([
    "📤 Upload",
    "📄 Pages & Schema",
    "✏️ Review",
    "📥 Export",
])


with tab_upload:
    if not uploaded_pdf:
        st.info("Upload a PDF to begin.")
    else:
        st.success(f"Loaded **{uploaded_pdf.name}** ({len(st.session_state.pdf_pages)} pages)")


with tab_pages:
    if not st.session_state.pdf_pages:
        st.info("Upload a PDF first.")
        st.stop()

    st.subheader("📄 Select Pages")

    pages = st.session_state.pdf_pages
    total_pages = len(pages)

    st.session_state.setdefault("selected_pages", set())
    st.session_state.setdefault("pages_confirmed", False)
    st.session_state.setdefault("extraction_complete", False)

    new_selection = set(st.session_state.selected_pages)

    col1, col2, _ = st.columns([1, 1, 6])
    with col1:
        if st.button("Select All"):
            st.session_state.selected_pages = set(range(1, total_pages + 1))
            st.session_state.pages_confirmed = False
            st.rerun()

    with col2:
        if st.button("Deselect All"):
            st.session_state.selected_pages = set()
            st.session_state.pages_confirmed = False
            st.rerun()

    cols = st.columns(3)
    for idx, page_num in enumerate(range(1, total_pages + 1)):
        with cols[idx % 3]:
            st.image(pages[page_num - 1], caption=f"Page {page_num}")
            checked = st.checkbox(
                f"Include Page {page_num}",
                value=(page_num in st.session_state.selected_pages),
                key=f"page_{page_num}",
                disabled=st.session_state.pages_confirmed,
            )
            if checked:
                new_selection.add(page_num)
            else:
                new_selection.discard(page_num)

    if not st.session_state.pages_confirmed:
        if st.button("Confirm Selected Pages", type="primary"):
            if not new_selection:
                st.warning("Select at least one page.")
                st.stop()

            st.session_state.selected_pages = new_selection
            st.session_state.pages_confirmed = True
            st.rerun()
    else:
        st.success("Pages confirmed.")

    st.divider()

    if not st.session_state.pages_confirmed:
        st.stop()

    if not st.session_state.extraction_complete:
        if st.button("🚀 Run Extraction", type="primary"):
            all_page_data = []
            progress = st.progress(0)
            status = st.empty()

            selected = sorted(st.session_state.selected_pages)

            for idx, page_num in enumerate(selected, start=1):
                status.write(f"Processing page {page_num}")
                page = pages[page_num - 1]

                schema = schemas.get(page_num)
                if not schema:
                    raise ValueError(f"No schema file found for page {page_num}")

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


with tab_review:
    if not st.session_state.extraction_complete:
        st.info("Run extraction first.")
        st.stop()

    data = st.session_state.extracted_data

    def pretty_label(key: str) -> str:
        key = str(key).split(".")[-1]
        key = key.replace("_", " ")
        return key.strip().title()

    def render_value(path: str, value):
        if isinstance(value, bool):
            return st.checkbox(pretty_label(path), value=value, key=path)

        if isinstance(value, dict):
            edited = {}
            with st.expander(pretty_label(path), expanded=False):
                for k, v in value.items():
                    child_path = f"{path}.{k}" if path else str(k)
                    edited[k] = render_value(child_path, v)
            return edited

        if isinstance(value, list):
            if value and all(isinstance(x, dict) for x in value):
                edited_list = []
                with st.expander(pretty_label(path), expanded=False):
                    for i, item in enumerate(value):
                        with st.expander(f"{pretty_label(path)} [{i+1}]", expanded=False):
                            edited_item = {}
                            for k, v in item.items():
                                child_path = f"{path}.{i}.{k}"
                                edited_item[k] = render_value(child_path, v)
                            edited_list.append(edited_item)
                return edited_list

            existing = "\n".join("" if x is None else str(x) for x in value)
            txt = st.text_area(pretty_label(path), value=existing, key=path, height=120)
            return [line for line in (l.strip() for l in txt.splitlines()) if line != ""]

        if isinstance(value, (int, float)):
            txt = st.text_input(pretty_label(path), value=str(value), key=path)
            try:
                return int(txt) if isinstance(value, int) else float(txt)
            except:
                return txt

        return st.text_input(pretty_label(path), value="" if value is None else str(value), key=path)

    edited = {}
    for section, fields in (data or {}).items():
        edited[section] = render_value(section, fields)

    st.session_state.extracted_data = edited
    st.success("Edits saved.")

with tab_export:
    if not st.session_state.extraction_complete:
        st.info("Complete extraction first.")
        st.stop()

    st.subheader("📥 Export")

    if st.button("Send to Therap"):
        base_name = st.session_state.get("base_name", "export")
        edited_data = st.session_state.get("extracted_data") or {}
        if not edited_data:
            st.info("No reviewed data available to export.")
            st.stop()


        with open("field_mapping.json", "r") as f:
            mapping_json = json.load(f)

        mapping = mapping_json.get("mappings", {})
        reverse_map = {v: k for k, v in mapping.items() if v}

        def flatten_json(data, parent_key="", sep="."):
            items = {}
            for k, v in data.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, dict):
                    items.update(flatten_json(v, new_key, sep))
                else:
                    items[new_key] = v
            return items

        flat_data = flatten_json(edited_data)
        extracted_df = pd.DataFrame([flat_data])

        idf_df = pd.read_excel(idf_path)
        idf_cols = list(idf_df.columns)

        official_df = pd.DataFrame(columns=idf_cols)

        for col in idf_cols:
            source_key = reverse_map.get(col)
            if source_key and source_key in extracted_df.columns:
                official_df[col] = extracted_df[source_key]
            else:
                official_df[col] = ""

        mapped_extract_cols = set(reverse_map.values())
        extra_cols = [c for c in extracted_df.columns if c not in mapped_extract_cols]
        extra_df = extracted_df[extra_cols] if extra_cols else pd.DataFrame()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        official_file = f"./{base_name}_import_ready_{ts}.xlsx"
        extra_file = f"./{base_name}_extra_fields_{ts}.xlsx"

        official_df.to_excel(official_file, index=False)

        if not extra_df.empty:
            extra_df.to_excel(extra_file, index=False)

        st.success("Files generated successfully")

        with open(official_file, "rb") as f:
            st.download_button(
                "⬇️ Download Import-Ready Excel (Therap Schema)",
                f,
                file_name=os.path.basename(official_file),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if not extra_df.empty:
            with open(extra_file, "rb") as f:
                st.download_button(
                    "⬇️ Download Extra Fields Excel",
                    f,
                    file_name=os.path.basename(extra_file),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
