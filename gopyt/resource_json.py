"""Internal admitted JSON token producers; complete parser integration is pending."""
import json
from gopyt.jsonc import ConvertFail
from gopyt.resource_text import _ChargedString, utf8_size


def string_token(text, start, budget, check=lambda: None):
    """Decode a quote-prefixed token, returning owned text and the next offset."""
    reservation = None
    raw = result = None
    transferred = False
    try:
        check()
        if not 0 <= start < len(text) or text[start] != '"':
            raise ConvertFail('syntax')
        # Find the closing quote without allocating a substring. Escaped quotes
        # are skipped; the strict scanner validates escape syntax afterwards.
        end = start + 1
        while end < len(text):
            if (end - start) % 4096 == 0:
                check()
            char = text[end]
            if char == '"':
                break
            end += 2 if char == '\\' else 1
        if end >= len(text):
            raise ConvertFail('syntax')
        capacity = 4 * (end - start + 1)
        reservation = budget.reserve(native_bytes=capacity)
        # Cover scanner writer replacement and substring temporaries separately
        # from the final owned copy. This is payload capacity, not heap metadata.
        with budget.reserve(native_bytes=3 * capacity):
            try:
                invalid = False
                try:
                    raw, next_offset = json.decoder.scanstring(text, start + 1, True)
                    utf8_size(raw, check)
                except (ValueError, UnicodeEncodeError):
                    invalid = True
                if invalid:
                    raise ConvertFail('syntax')
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result, next_offset
            finally:
                raw = None
    finally:
        text = result = None
        if reservation is not None and not transferred:
            reservation.release()
