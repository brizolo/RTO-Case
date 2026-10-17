import re
 
UNIT_TO_KG = {
    "kg": 1.0, "kgs": 1.0, "кг": 1.0,
    "g": 0.001, "gr": 0.001, "grs": 0.001, "gm": 0.001, "grm": 0.001,
    "г": 0.001, "гр": 0.001,
    "l": 1.0, "lt": 1.0, "ltr": 1.0, "л": 1.0,
    "ml": 0.001, "мл": 0.001,
    "cl": 0.01,
}
 
_UNITS = "|".join(sorted(map(re.escape, UNIT_TO_KG), key=len, reverse=True))
 
_NUM = r"\d+(?:[.,]\d+)?"

PACK_COUNT_FIRST = re.compile(
    rf"(?P<count>\d+)\s*[x×]\s*(?P<num>{_NUM})\s*(?P<unit>{_UNITS})(?![a-zа-я])",
    re.IGNORECASE,
)
PACK_SIZE_FIRST = re.compile(
    rf"(?P<num>{_NUM})\s*(?P<unit>{_UNITS})\s*[x×]\s*(?P<count>\d+)(?![\d])",
    re.IGNORECASE,
)
SINGLE = re.compile(
    rf"(?<![\w.,])(?P<num>{_NUM})\s*(?P<unit>{_UNITS})(?![a-zа-я])",
    re.IGNORECASE,
)
 
 
def _to_float(text):
    return float(text.replace(",", "."))
 
CAPACITY_WORDS = re.compile(
    r"lixo|śmieci|smieci|menajer|poubelle|smeće|smece|garbage|trash|bin bag|мусор|смітт|kese za|"
    r"mikrofal|microwave|micro-ondes|frigider|lodówk|fridge|kettle|czajnik|fierb",
    re.IGNORECASE,
)
SOLD_BY_KG = re.compile(r"\bper\s*kg\b|/\s*kg\b|\bza\s*kg\b|\bза\s*кг\b|(?<![\d.,])\s+kg\s*$",
                        re.IGNORECASE)
WEIGHT_RANGE = re.compile(rf"{_NUM}\s*-\s*{_NUM}\s*(?:{_UNITS})", re.IGNORECASE)
 
 
def parse_unit_weight(name):
    if not isinstance(name, str):
        return None, None
 
    if CAPACITY_WORDS.search(name) or WEIGHT_RANGE.search(name):
        return None, None
 
    m = SOLD_BY_KG.search(name)
    if m:
        return 1.0, m.group(0).strip()
 
    name = re.sub(r"\b0 (\d{1,3})\s*(?=[lL]\b)", r"0.\1", name)
 
    for pattern in (PACK_COUNT_FIRST, PACK_SIZE_FIRST):
        m = pattern.search(name)
        if m:
            kg = int(m["count"]) * _to_float(m["num"]) * UNIT_TO_KG[m["unit"].lower()]
            return kg, m.group(0)

    matches = list(SINGLE.finditer(name))
    if matches:
        m = matches[-1]
        kg = _to_float(m["num"]) * UNIT_TO_KG[m["unit"].lower()]
        return kg, m.group(0)
 
    return None, None
 
 
if __name__ == "__main__":
    
    examples = [
        "Leite Uht Gresso Meio Gordo 1 L",         
        "Iogurte Auchan Natural 4X125G",           
        "Cement portlandzki CEM II 32,5R 25 kg",    
        "Schweppes Ginger Ale napój gaz 0,85L PET", 
        "Royco Beef Cube 4G X 28",                 
        "Бешбармак из конины (600г)",              
        "Mama Gold Rice 50kg",                      
        "Telewizor Samsung 55\" UE55DU7172 4K Smart TV",
        "DUNHILL Fine Cut Master Blend",            
        "Saci Menajeri Carrefour 240 L, 10 Bucati", 
        "Couches Bébé Taille 4 9-18Kg X68",         
        "Sok Gazirani 0 33 L Coca Cola Zero",       
        "Nectarine vrac per kg",                   
        "Coxas De Frango Auchan Kg",                
    ]
    for name in examples:
        kg, matched = parse_unit_weight(name)
        print(f"{name[:45]:45} -> {kg!s:7} ({matched})")
 