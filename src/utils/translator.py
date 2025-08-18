import re

import deepl

from .. import config

translator = None
# 설정 파일에 DeepL API 키가 있을 경우에만 번역기 객체를 초기화합니다.
if config.DEEPL_API_KEY:
    try:
        translator = deepl.Translator(config.DEEPL_API_KEY)
        print("DeepL 번역기가 성공적으로 초기화되었습니다.")
    except Exception as e:
        print(f"DeepL 번역기 초기화 실패: {e}")


def is_mostly_english(text: str) -> bool:
    """
    문자열에 영어 알파벳이 한글보다 더 많이 포함되어 있는지 확인하여
    번역 필요 여부를 결정합니다.
    """
    if not text:
        return False

    # 영어 알파벳, 숫자, 일반 특수문자 카운트
    english_chars = len(
        re.findall(r'[a-zA-Z0-9\s\.,!@#$%^&*()_+-=<>?:"\'/`~\[\]{}|\\-]', text)
    )
    # 한글 문자 카운트
    korean_chars = len(re.findall(r"[ㄱ-ㅣ가-힣]", text))

    # 영어 문자가 한글 문자보다 많으면 영어 메시지로 간주
    return english_chars > korean_chars


def translate_to_korean_if_needed(message: str) -> str:
    """
    주어진 메시지가 주로 영어로 구성되어 있을 경우에만 한국어로 번역합니다.
    """
    # 번역기 객체가 없거나, 메시지가 영어가 아니면 원본을 그대로 반환
    if not translator or not is_mostly_english(message):
        return message

    try:
        # DeepL API를 통해 텍스트를 한국어로 번역
        result = translator.translate_text(message, target_lang="KO")
        return result.text
    except Exception as e:
        print(f"DeepL 번역 실패: {e}")
        # 번역 실패 시 원본 메시지에 실패 정보를 추가하여 반환
        return f"{message}\n\n(DeepL 번역 실패)"
