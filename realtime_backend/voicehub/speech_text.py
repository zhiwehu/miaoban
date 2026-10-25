"""Keep UI emoji out of speech engines that pronounce their accessibility names."""
import re

PICTOGRAPHS = re.compile(
    '[\U0001f000-\U0001faff\u2600-\u27bf\u231a\u231b'
    '\u23e9-\u23f3\u23f8-\u23fa\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe]'
)
PRESENTATION = re.compile('[\ufe0e\ufe0f\u200d\u20e3\U000e0020-\U000e007f]')


def spoken_text(text: str) -> str:
    text = PICTOGRAPHS.sub(' ', text)
    text = PRESENTATION.sub('', text)
    return re.sub(r'[ \t]+', ' ', text).strip()
