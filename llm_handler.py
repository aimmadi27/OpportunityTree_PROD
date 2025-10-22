import os
import json
import streamlit as st
from dotenv import load_dotenv
from json_repair import repair_json
import google.generativeai as genai


def get_env_var(name: str):
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name)

class LLMHandler:
    def __init__(self):
        """
        Initialize the LLM using environment variables.

        Required in .env:
            LLM_MODEL_NAME       -> name of the model (string)
            LLM_API_KEY_ENV      -> name of the env variable that stores API key
        Optional (user-defined):
            Any other vars needed for your chosen provider (e.g., API base URL)
        """

        load_dotenv()

        self.model_name = get_env_var("LLM_MODEL_NAME")
        self.api_key = get_env_var("LLM_API_KEY_ENV")

        if not self.model_name or not self.api_key:
            raise RuntimeError(
                "Missing required environment variables: "
                "LLM_MODEL_NAME and/or LLM_API_KEY_ENV."
            )

        # -------------------------------------------------------------
        # 🔧 USER CONFIGURATION SECTION
        # -------------------------------------------------------------
        # Import and initialize your model explicitly here.
        #
        # Example for Google Gemini:
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)
        #
        # Example for OpenAI:
        #   import openai
        #   openai.api_key = self.api_key
        #   self.model = openai
        #
        # Example for Anthropic Claude:
        #   from anthropic import Anthropic
        #   self.model = Anthropic(api_key=self.api_key)
        #
        # -------------------------------------------------------------
        #     Developers must uncomment and modify this section
        #     according to their chosen provider.
        # -------------------------------------------------------------

    def generate_json(self, schema_text, page_prompt, image_bytes):
        try:
            response = self.model.generate_content(
                [
                    {"role": "user", "parts": [
                        {"text": schema_text},
                        {"text": page_prompt},
                        {"mime_type": "image/png", "data": image_bytes}
                    ]}
                ],
                generation_config={
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "response_mime_type": "application/json",
                },
                request_options={"timeout": 180}
            )

            text_output = getattr(response, "text", str(response))
            try:
                return json.loads(text_output)
            except json.JSONDecodeError:
                start, end = text_output.find("{"), text_output.rfind("}")
                if start != -1 and end != -1:
                    candidate = text_output[start:end + 1]
                    return json.loads(repair_json(candidate))
                raise

        except Exception as e:
            raise RuntimeError(f"LLM generation failed: {e}")
