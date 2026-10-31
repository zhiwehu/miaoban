"""Incremental speech text boundary: consume metadata before emitting sentences."""
import re
from dialogue_expression import parse_reply, EMOTIONS

class SpeechSentences:
    def __init__(self, max_chars=40):
        self.buffer = ''
        self.emotion = 'neutral'
        self.prefix = True
        self.thinking = False
        self.max_chars = max_chars

    def feed(self, text, final=False):
        self.buffer += text
        if self.prefix:
            self.buffer = self.buffer.lstrip()
            if self.thinking:
                if '</think>' not in self.buffer:
                    self.buffer = self.buffer[-8:]
                    return []
                self.buffer = self.buffer.split('</think>', 1)[1].lstrip()
                self.thinking = False
            if not final and (not self.buffer or '<think>'.startswith(self.buffer)):
                return []
            if self.buffer.startswith('<think>'):
                self.thinking = True
                self.buffer = self.buffer[7:]
                return self.feed('', final)
            if self.buffer.startswith('['):
                if ']' not in self.buffer and not final and len(self.buffer) < 64:
                    return []
                self.buffer, self.emotion = parse_reply(self.buffer)
            self.prefix = False
        # Models sometimes put metadata at the end or inside the spoken text.
        # Remove only reserved tags, retaining an incomplete tag until more tokens arrive.
        pattern = r'\[(?:emotion:[^\]\r\n]*|' + '|'.join(EMOTIONS) + r')\]'
        def consume(match):
            _, self.emotion = parse_reply(match.group(0))
            return ''
        self.buffer = re.sub(pattern, consume, self.buffer, flags=re.IGNORECASE)
        result = []
        while self.buffer:
            match = re.search(r'[。！？!?；;\n]|\.(?=\s)', self.buffer)
            end = match.end() if match else None
            # Prefer complete sentences; bound a model's unpunctuated output.
            if end is None or end > self.max_chars:
                end = self.max_chars if len(self.buffer) >= self.max_chars else None
            if end is None:
                if final: end = len(self.buffer)
                else: break
            start = self.buffer.rfind('[')
            if start >= 0 and ']' not in self.buffer[start:]:
                tail = self.buffer[start:].lower()
                reserved = ['[emotion:'] + ['[' + name + ']' for name in EMOTIONS]
                if any(tag.startswith(tail) or tail.startswith(tag) for tag in reserved):
                    if final:
                        self.buffer = self.buffer[:start]
                        end = min(end, len(self.buffer))
                    elif end > start:
                        end = start
                    if end == 0:
                        break
            sentence, self.buffer = self.buffer[:end].strip(), self.buffer[end:]
            if sentence: result.append(sentence)
        return result

    def finish(self):
        return self.feed('', final=True)
