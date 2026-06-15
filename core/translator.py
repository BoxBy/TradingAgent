import re
import config

translator = None

# Module-level key rotation counter for fallback_translate_gemma
_key_rotation_index = 0

if config.DEEPL_API_KEY:
    try:
        import deepl
        translator = deepl.Translator(config.DEEPL_API_KEY)
        print("DeepL 번역기가 성공적으로 초기화되었습니다.")
    except Exception as e:
        print(f"DeepL 번역기 초기화 실패: {e}")

def is_mostly_english(text: str) -> bool:
    if not text:
        return False
    # Check if text has significant English content (words, sentences)
    # Korean text typically has more Korean characters than English words
    korean_chars = len(re.findall(r"[ㄱ-ㅣ가-힣]", text))
    english_words = len(re.findall(r'[a-zA-Z]{3,}', text))  # 3+ letter English words
    total_chars = len(text)
    # If Korean chars > 5% of total, it's Korean (avoid false positive for emoji/numbers)
    if total_chars > 0 and korean_chars / total_chars > 0.05:
        return False
    return english_words > 3  # Has multiple English words

# Newline preservation token - use unicode to avoid DeepL processing
NL_TOKEN = "\u0003"  # End of Text character (invisible to most systems)

def translate_to_korean_if_needed(message: str) -> str:
    if not translator:
        return message  # 번역기가 없으면 원문 그대로 반환

    try:
        # 영어로 판별되는 경우에만 번역 시도
        if is_mostly_english(message):
            # Line-by-line translation for newline preservation
            has_newlines = '\n' in message
            if has_newlines:
                lines = message.split('\n')
                translated_lines = []
                for line in lines:
                    if line.strip():  # Only translate non-empty lines
                        result = translator.translate_text(line, target_lang="KO")
                        translated_lines.append(result.text)
                    else:
                        translated_lines.append('')  # Preserve empty lines
                translated_text = '\n'.join(translated_lines)
                return translated_text
            else:
                # No newlines, translate as is
                result = translator.translate_text(
                    message,
                    target_lang="KO"
                )
                if result.text and result.text != message:
                    return result.text
        # 번역이 필요 없으면 원문 그대로 반환
        return message
    except Exception as e:
        # DeepL 실패 시 Gemma fallback 시도
        print(f"[Translator] DeepL 번역 오류 ({e}). Gemma-4 폴백 시도 중...")
        try:
            return fallback_translate_gemma(message)
        except Exception as fallback_e:
            print(f"[Translator] Gemma 폴백 실패: {fallback_e}")
            return message

def fallback_translate_gemma(text: str) -> str:
    import os
    from openai import OpenAI
    import config
    from dotenv import load_dotenv

    # Ensure env variables are loaded
    if hasattr(config, "ENV_PATH"):
        load_dotenv(dotenv_path=config.ENV_PATH)
    else:
        load_dotenv()
    
    # Try using configured Gemini API keys with rotation
    global _key_rotation_index
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and hasattr(config, "GEMINI_API_KEYS") and config.GEMINI_API_KEYS:
        keys = config.GEMINI_API_KEYS
        idx = _key_rotation_index % len(keys)
        api_key = keys[idx]
        _key_rotation_index += 1
        
    if not api_key:
        api_key = os.getenv("GOOGLE_API_KEY_1")
        
    if not api_key:
        print("[Translator] No fallback GEMINI_API_KEY found.")
        return text

    # Google's OpenAI-compatible endpoint for Gemma
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    model = "gemma-4-31b-it"

    try:
        prompt = f"Translate to Korean. Output ONLY the Korean translation, nothing else.\n\n{text}"
        
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        # Strip any <thought>...</thought> tags from reasoning models
        import re as _re
        cleaned = _re.sub(r'<thought>.*?</thought>\s*', '', raw, flags=_re.DOTALL).strip()
        return cleaned if cleaned else text
    except Exception as e:
        print(f"[Translator] Gemma-4-31b-it fallback error: {e}")
        return text
