import re
import config

translator = None

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
        print(f"[Translator] DeepL 번역 오류 ({e}). Gemma-3 폴백 시도 중...")
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
    
    # Try using configured Gemini API keys
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and hasattr(config, "GEMINI_API_KEYS") and config.GEMINI_API_KEYS:
        api_key = config.GEMINI_API_KEYS[0]
        
    if not api_key:
        api_key = os.getenv("GOOGLE_API_KEY_1")
        
    if not api_key:
        print("[Translator] No fallback GEMINI_API_KEY found.")
        return text

    # Google's OpenAI-compatible endpoint for Gemma
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    model = "gemma-3-27b-it"

    try:
        # Gemma-3-27b-it on Google endpoints does not support system instructions
        prompt = f"You are a professional English to Korean translator. Translate the following text directly to Korean without any conversational fillers or explanations.\n\n{text}"
        
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        translated = response.choices[0].message.content.strip()
        return translated if translated else text
    except Exception as e:
        print(f"[Translator] Gemma 3 27b-it fallback error: {e}")
        return text
