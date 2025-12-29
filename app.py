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

try:
    llm = LLMHandler()
except Exception as e:
    st.error(f"Failed to initialize LLM: {e}")
    st.stop()

SCHEMA_DIR = "./schemas"
schema_files = sorted(pathlib.Path(SCHEMA_DIR).glob("*.json"))
if not schema_files:
    st.error("No schemas found in ./schemas/")
    st.stop()

schemas = {f.stem: json.load(open(f, "r", encoding="utf-8")) for f in schema_files}

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
    new_selection = set()

    col1, col2, _ = st.columns([1, 1, 6])
    with col1:
        if st.button("Select All"):
            st.session_state.selected_pages = set(
                range(1, len(st.session_state.pdf_pages) + 1)
            )
            st.rerun()

    with col2:
        if st.button("Deselect All"):
            st.session_state.selected_pages = set()
            st.rerun()

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

with tab_review:
    if not st.session_state.extraction_complete:
        st.info("Run extraction first.")
        st.stop()

    final_json = st.session_state.extracted_data
    edited_data = {}

    def pretty_label(key: str) -> str:
        key = key.split(".")[-1]
        key = key.replace("_", " ")
        return key.strip().title()

    for section, fields in final_json.items():

        with st.expander(pretty_label(section), expanded=False):

            if isinstance(fields, dict):
                section_data = {}

                for field, value in fields.items():

                    if isinstance(value, dict):
                        if "properties" in value and isinstance(value["properties"], dict):
                            value = value["properties"]
                        st.markdown(f"**{pretty_label(field)}:**")
                        subdata = {}

                        for subfield, subval in value.items():
                            subdata[subfield] = st.text_input(
                                pretty_label(subfield),
                                value=subval or "",
                                key=f"{section}_{field}_{subfield}"
                            )

                        section_data[field] = subdata

                    elif isinstance(value, bool):
                        section_data[field] = st.checkbox(
                            pretty_label(field),
                            value=value,
                            key=f"{section}_{field}"
                        )

                    elif isinstance(value, list):
                        section_data[field] = st.text_area(
                            pretty_label(field),
                            value=", ".join(map(str, value)),
                            key=f"{section}_{field}"
                        )

                    else:
                        section_data[field] = st.text_input(
                            pretty_label(field),
                            value=value or "",
                            key=f"{section}_{field}"
                        )

                edited_data[section] = section_data

            elif isinstance(fields, list) and fields and isinstance(fields[0], dict):
                list_items = []

                for idx, item in enumerate(fields, start=1):
                    st.markdown(f"**Entry {idx}:**")
                    item_data = {}

                    for subfield, subval in item.items():
                        item_data[subfield] = st.text_input(
                            pretty_label(subfield),
                            value=str(subval) if subval is not None else "",
                            key=f"{section}_{idx}_{subfield}"
                        )

                    list_items.append(item_data)

                edited_data[section] = list_items

            else:
                edited_data[section] = st.text_input(
                    pretty_label(section),
                    value=str(fields) if fields is not None else "",
                    key=f"{section}_value"
                )

    st.session_state.extracted_data = edited_data

with tab_export:
    if not st.session_state.extraction_complete:
        st.info("Complete extraction first.")
        st.stop()

    st.subheader("📥 Export")

    if st.button("Send to Therap"):
        base_name = st.session_state.get("base_name", "export")

        if not edited_data:
            st.info("No reviewed data available to export.")
            st.stop()

        idf_path = "./IDF_Import_ProviderExcel_TOT-AZ_20251019.xlsx"
        if not os.path.exists(idf_path):
            st.info("⚠️ IDF provider Excel not found. Add it to the app folder.")
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

        st.success("✅ Files generated successfully")

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
