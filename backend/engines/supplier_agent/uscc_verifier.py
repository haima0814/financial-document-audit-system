"""
backend/engines/supplier_agent/uscc_verifier.py
GB 32100-2015 统一社会信用代码 18 位校验码算法
"""
import re

CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
CHAR_MAP = {c: i for i, c in enumerate(CHARS)}
WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]

def verify_uscc_checksum(uscc: str) -> bool:
    """严格校验统一社会信用代码校验位 (GB 32100-2015)"""
    cleaned = uscc.strip().upper()
    if len(cleaned) != 18:
        return False
    if not re.match(r'^[0-9A-HJ-NPQRTUWXY]{18}$', cleaned):
        return False

    total = 0
    for i in range(17):
        c = cleaned[i]
        if c not in CHAR_MAP:
            return False
        total += CHAR_MAP[c] * WEIGHTS[i]

    remainder = total % 31
    check_index = (31 - remainder) % 31
    expected_check_char = CHARS[check_index]

    return cleaned[17] == expected_check_char
