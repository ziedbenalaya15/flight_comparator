"""Expansion pays -> aéroports principaux (codes IATA).

La zone de départ accepte soit des codes aéroport/ville IATA (3 lettres),
soit un code pays ISO (2 lettres) développé via cette table.
"""

COUNTRY_AIRPORTS: dict[str, list[str]] = {
    "FR": ["PAR", "NCE", "LYS", "MRS", "TLS", "BOD", "NTE", "LIL", "SXB", "MPL"],
    "BE": ["BRU", "CRL"],
    "DE": ["FRA", "MUC", "BER", "DUS", "HAM", "CGN", "STR"],
    "NL": ["AMS", "EIN"],
    "CH": ["ZRH", "GVA", "BSL"],
    "ES": ["MAD", "BCN", "AGP", "VLC", "SVQ", "BIO"],
    "IT": ["ROM", "MIL", "VCE", "NAP", "BLQ", "TRN"],
    "GB": ["LON", "MAN", "EDI", "BHX", "GLA"],
    "PT": ["LIS", "OPO", "FAO"],
    "AT": ["VIE"],
    "IE": ["DUB"],
    "LU": ["LUX"],
    "TN": ["TUN", "NBE", "DJE", "SFA"],
    "MA": ["CMN", "RAK", "RBA", "TNG", "AGA", "FEZ"],
    "DZ": ["ALG", "ORN", "CZL"],
    "US": ["NYC", "LAX", "MIA", "SFO", "ORD", "BOS", "WAS", "SEA", "ATL"],
    "CA": ["YTO", "YMQ", "YVR"],
    "TR": ["IST", "SAW", "AYT"],
    "AE": ["DXB", "AUH", "SHJ"],
    "QA": ["DOH"],
    "JP": ["TYO", "OSA", "NGO", "FUK"],
    "KR": ["SEL", "PUS"],
    "TH": ["BKK", "HKT", "CNX"],
    "SG": ["SIN"],
    "MY": ["KUL"],
    "ID": ["JKT", "DPS"],
    "VN": ["SGN", "HAN"],
    "CN": ["BJS", "SHA", "CAN", "SZX"],
    "IN": ["DEL", "BOM", "BLR", "MAA"],
    "BR": ["SAO", "RIO"],
    "MX": ["MEX", "CUN"],
    "AU": ["SYD", "MEL", "BNE", "PER"],
    "NZ": ["AKL"],
    "ZA": ["JNB", "CPT"],
    "EG": ["CAI", "HRG", "SSH"],
}


def expand_departure_zone(entries: list[str]) -> list[str]:
    """Développe chaque entrée : code pays (2 lettres) -> aéroports, sinon code IATA tel quel."""
    airports: list[str] = []
    for entry in entries:
        code = entry.strip().upper()
        if not code:
            continue
        if len(code) == 2 and code in COUNTRY_AIRPORTS:
            airports.extend(COUNTRY_AIRPORTS[code])
        else:
            airports.append(code)
    # dédoublonne en préservant l'ordre
    return list(dict.fromkeys(airports))
