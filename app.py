import streamlit as st
import json
import pathlib
import tempfile
import os
from dotenv import load_dotenv
from pdf2image import convert_from_path
import pandas as pd

from ocr_extractor import extract_page_json, merge_page_results
from llm_handler import LLMHandler

st.set_page_config(page_title="Handwritten Form Extractor", page_icon="📝", layout="wide")

st.markdown("""
    <style>
        body { background-color: #f5f7fa; }
        .main {
            background-color: #ffffff;
            border-radius: 12px;
            padding: 25px;
            box-shadow: 0px 4px 15px rgba(0,0,0,0.1);
        }
        h1, h2, h3 {
            color: #2a4365;
        }
        .stButton>button {
            background-color: #2b6cb0;
            color: white;
            border-radius: 10px;
            padding: 0.6em 1.2em;
            font-weight: 600;
            border: none;
        }
        .stButton>button:hover {
            background-color: #2c5282;
        }
        .section-box {
            background-color: #f7fafc;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid #e2e8f0;
        }
    </style>
""", unsafe_allow_html=True)

st.title("📝 Handwritten Form Extractor")
st.caption("Extract, review, and export handwritten form data powered by LLMs.")


load_dotenv()

try:
    llm = LLMHandler()
except Exception as e:
    st.error(f"⚠️ Failed to initialize LLM: {e}")
    st.stop()


uploaded_pdf = st.file_uploader("### Upload a filled PDF form", type=["pdf"])

if uploaded_pdf:
    temp_pdf_path = pathlib.Path(f"./temp_{uploaded_pdf.name}")
    with open(temp_pdf_path, "wb") as f:
        f.write(uploaded_pdf.read())

    schema_path = pathlib.Path("./ocr_schema.json")
    if not schema_path.exists():
        st.error("⚠️ Schema file (ocr_schema.json) not found.")
        st.stop()

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    schema_text = json.dumps(schema, indent=2, ensure_ascii=False)

    if "extracted_data" not in st.session_state or st.session_state.get("last_pdf") != uploaded_pdf.name:
        pages = convert_from_path(temp_pdf_path, dpi=150)
        st.success(f"✅ Converted {len(pages)} pages successfully.")

        all_page_data = []
        progress = st.progress(0)
        status = st.empty()

        for i, page in enumerate(pages, start=1):
            status.write(f"🔍 Processing page {i}/{len(pages)} ...")

            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                page.save(tmp.name, "PNG")
                with open(tmp.name, "rb") as img_file:
                    img_bytes = img_file.read()
                try:
                    page_json = extract_page_json(llm, img_bytes, i, schema_text)
                except Exception as e:
                    st.warning(f"⚠️ LLM error on page {i}: {e}")
                    page_json = {}
                all_page_data.append(page_json)

            progress.progress(i / len(pages))

        final_json = merge_page_results(all_page_data)
        st.success("✅ Extraction complete!")

        st.session_state.extracted_data = final_json
        st.session_state.last_pdf = uploaded_pdf.name

    else:
        final_json = st.session_state.extracted_data
        st.success("✅ Using previously extracted results (no extra Gemini calls).")

    st.markdown("### Review and Edit Extracted Data")
    st.caption("You can make corrections before exporting to Therap.")

    edited_data = {}

    for section, fields in final_json.items():
        
        if isinstance(fields, dict):
            with st.expander(f"{section}", expanded=False):
                section_data = {}
                for field, value in fields.items():

                    if isinstance(value, bool):
                        section_data[field] = st.checkbox(field, value=value)
                    elif isinstance(value, list):
                        section_data[field] = st.text_area(
                            field, value=", ".join(map(str, value))
                        )
                    elif isinstance(value, dict):
                        st.markdown(f"**{field}:**")
                        subdata = {}
                        for subfield, subval in value.items():
                            subdata[subfield] = st.text_input(
                                f"{field} → {subfield}", value=subval or ""
                            )
                        section_data[field] = subdata
                    else:
                        section_data[field] = st.text_input(field, value=value or "")
                edited_data[section] = section_data

        elif isinstance(fields, list) and fields and isinstance(fields[0], dict):
            with st.expander(f"{section}", expanded=False):
                list_items = []

                for idx, item in enumerate(fields, start=1):
                    st.markdown(f"**Entry {idx}:**")
                    item_data = {}

                    for subfield, subval in item.items():
                        item_data[subfield] = st.text_input(
                            f"{section} → {subfield} ({idx})",
                            value=str(subval) if subval is not None else "",
                            key=f"{section}_{idx}_{subfield}",
                        )

                    list_items.append(item_data)

                edited_data[section] = list_items

        else:
            edited_data[section] = st.text_input(
                section, value=str(fields) if fields is not None else ""
            )


    if st.button("Send to Therap"):
        def flatten_json(data, parent_key='', sep='.'):
            items = []
            for k, v in data.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, dict):
                    items.extend(flatten_json(v, new_key, sep=sep).items())
                else:
                    items.append((new_key, v))
            return dict(items)

        flat_data = flatten_json(edited_data)
        df = pd.DataFrame([flat_data])

        excel_path = f"./{temp_pdf_path.stem}_reviewed.xlsx"
        df.to_excel(excel_path, index=False)

        with open(excel_path, "rb") as f:
            st.download_button(
                label="Download Reviewed Excel File",
                data=f,
                file_name=f"{temp_pdf_path.stem}_reviewed.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        st.success("✅ Data exported successfully! File ready for download.")