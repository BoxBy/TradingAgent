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
    english_chars = len(re.findall(r'[a-zA-Z0-9\s\.,!@#$%^&*()_+-=<>?:"\'/`~\[\]{}|\\-]', text))
    korean_chars = len(re.findall(r"[ㄱ-ㅣ가-힣]", text))
    return english_chars > korean_chars

def translate_to_korean_if_needed(message: str) -> str:
    if not translator or not is_mostly_english(message):
        return message
    try:
        result = translator.translate_text(message, target_lang="KO")
        return result.text
    except Exception as e:
        return f"번역 실패\n{message}"
