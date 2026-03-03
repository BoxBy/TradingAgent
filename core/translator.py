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

def translate_to_korean_if_needed(message: str) -> str:
    if not translator or not is_mostly_english(message):
        return message
    try:
        result = translator.translate_text(message, target_lang="KO")
        return result.text
    except Exception as e:
        return f"번역 실패\n{message}"
