"""
NIFTY Top 200 universe manifest (single, auditable constituent source)

This module is the CANONICAL, deterministic definition of the NIFTY Top 200
stock universe the future continuous intraday market scanner (Checkpoint 19.x)
will monitor. It is an ENGINE-level configuration module consumed by
:mod:`engine.config.universe`; the dashboard and the historical-data layer
reuse it without ever duplicating the constituent list.

SOURCE (authoritative): the official NSE "NIFTY 200 Index" constituent file
https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv
as published on the NSE NIFTY 200 index page
(https://www.nseindia.com/static/products-services/indices-nifty200-index).

PROVENANCE of the embedded snapshot:
* CSV source URL ....... https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv
* Retrieved ............ 2026-09-05 (this checkpoint)
* NSE "Updated on" ..... 22/04/2026
* HTTP Last-Modified ... Sat, 05 Sep 2026 03:31:32 GMT
* CSV byte length ...... 13081
* CSV SHA-256 .......... 76b8b127931953ce7e5e5511c99c3b73775140eeb83b6b293085b4a9483dce1a
* Data rows ............ 200 (exactly the published membership)

Raw bytes archived at scripts/checkpoint19_data/ind_nifty200list.csv (JSON at
ind_nifty200list.json) so the snapshot stays byte-comparable with upstream.

Validation performed: exactly 200 rows, no duplicate Symbol/ISIN, every row
has non-empty Symbol/Company/Industry/Series(all EQ)/ISIN, every Symbol matches
[A-Z0-9&.-]+,
every ISIN matches INE prefix + 9 alphanumeric + 1 digit.

NOTE: point-in-time snapshot; NSE re-constitutes semi-annually. A FUTURE
checkpoint owns re-fetch/re-validate/re-embed. This module never contacts the
network and has no look-ahead or trading logic; it is DESCRIPTIVE configuration
only.

"""
from __future__ import annotations

#: Version of the embedded constituent snapshot (NSE "Updated on" label).
NIFTY200_MANIFEST_VERSION: str = "2026-04-22-nse"

#: SHA-256 of the raw official NSE CSV file (13081 bytes) this manifest was generated from.
NIFTY200_CSV_SHA256: str = "76b8b127931953ce7e5e5511c99c3b73775140eeb83b6b293085b4a9483dce1a"

#: Fully-qualified source URL of the official constituent file.
NIFTY200_SOURCE_URL: str = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"

#: One (symbol, company_name, isin) row per official NIFTY 200 constituent;
#: alphabetised by symbol (deterministic, matches the archived NSE file).
NIFTY200_CONSTITUENTS: tuple[tuple[str, str, str], ...] = (
    ("360ONE", "360 ONE WAM Ltd.", "INE466L01038"),
    ("ABB", "ABB India Ltd.", "INE117A01022"),
    ("ABCAPITAL", "Aditya Birla Capital Ltd.", "INE674K01013"),
    ("ADANIENSOL", "Adani Energy Solutions Ltd.", "INE931S01010"),
    ("ADANIENT", "Adani Enterprises Ltd.", "INE423A01024"),
    ("ADANIGREEN", "Adani Green Energy Ltd.", "INE364U01010"),
    ("ADANIPORTS", "Adani Ports and Special Economic Zone Ltd.", "INE742F01042"),
    ("ADANIPOWER", "Adani Power Ltd.", "INE814H01029"),
    ("ALKEM", "Alkem Laboratories Ltd.", "INE540L01014"),
    ("AMBUJACEM", "Ambuja Cements Ltd.", "INE079A01024"),
    ("APLAPOLLO", "APL Apollo Tubes Ltd.", "INE702C01027"),
    ("APOLLOHOSP", "Apollo Hospitals Enterprise Ltd.", "INE437A01024"),
    ("ASHOKLEY", "Ashok Leyland Ltd.", "INE208A01029"),
    ("ASIANPAINT", "Asian Paints Ltd.", "INE021A01026"),
    ("ASTRAL", "Astral Ltd.", "INE006I01046"),
    ("ATGL", "Adani Total Gas Ltd.", "INE399L01023"),
    ("AUBANK", "AU Small Finance Bank Ltd.", "INE949L01017"),
    ("AUROPHARMA", "Aurobindo Pharma Ltd.", "INE406A01037"),
    ("AXISBANK", "Axis Bank Ltd.", "INE238A01034"),
    ("BAJAJ-AUTO", "Bajaj Auto Ltd.", "INE917I01010"),
    ("BAJAJFINSV", "Bajaj Finserv Ltd.", "INE918I01026"),
    ("BAJAJHLDNG", "Bajaj Holdings & Investment Ltd.", "INE118A01012"),
    ("BAJFINANCE", "Bajaj Finance Ltd.", "INE296A01032"),
    ("BANKBARODA", "Bank of Baroda", "INE028A01039"),
    ("BANKINDIA", "Bank of India", "INE084A01016"),
    ("BDL", "Bharat Dynamics Ltd.", "INE171Z01026"),
    ("BEL", "Bharat Electronics Ltd.", "INE263A01024"),
    ("BHARATFORG", "Bharat Forge Ltd.", "INE465A01025"),
    ("BHARTIARTL", "Bharti Airtel Ltd.", "INE397D01024"),
    ("BHEL", "Bharat Heavy Electricals Ltd.", "INE257A01026"),
    ("BIOCON", "Biocon Ltd.", "INE376G01013"),
    ("BLUESTARCO", "Blue Star Ltd.", "INE472A01039"),
    ("BOSCHLTD", "Bosch Ltd.", "INE323A01026"),
    ("BPCL", "Bharat Petroleum Corporation Ltd.", "INE029A01011"),
    ("BRITANNIA", "Britannia Industries Ltd.", "INE216A01030"),
    ("BSE", "BSE Ltd.", "INE118H01025"),
    ("CANBK", "Canara Bank", "INE476A01022"),
    ("CGPOWER", "CG Power and Industrial Solutions Ltd.", "INE067A01029"),
    ("CHOLAFIN", "Cholamandalam Investment and Finance Company Ltd.", "INE121A01024"),
    ("CIPLA", "Cipla Ltd.", "INE059A01026"),
    ("COALINDIA", "Coal India Ltd.", "INE522F01014"),
    ("COCHINSHIP", "Cochin Shipyard Ltd.", "INE704P01025"),
    ("COFORGE", "Coforge Ltd.", "INE591G01025"),
    ("COLPAL", "Colgate Palmolive (India) Ltd.", "INE259A01022"),
    ("CONCOR", "Container Corporation of India Ltd.", "INE111A01025"),
    ("COROMANDEL", "Coromandel International Ltd.", "INE169A01031"),
    ("CUMMINSIND", "Cummins India Ltd.", "INE298A01020"),
    ("DABUR", "Dabur India Ltd.", "INE016A01026"),
    ("DIVISLAB", "Divi's Laboratories Ltd.", "INE361B01024"),
    ("DIXON", "Dixon Technologies (India) Ltd.", "INE935N01020"),
    ("DLF", "DLF Ltd.", "INE271C01023"),
    ("DMART", "Avenue Supermarts Ltd.", "INE192R01011"),
    ("DRREDDY", "Dr. Reddy's Laboratories Ltd.", "INE089A01031"),
    ("EICHERMOT", "Eicher Motors Ltd.", "INE066A01021"),
    ("ENRIN", "Siemens Energy India Ltd.", "INE1NPP01017"),
    ("ETERNAL", "Eternal Ltd.", "INE758T01015"),
    ("EXIDEIND", "Exide Industries Ltd.", "INE302A01020"),
    ("FEDERALBNK", "Federal Bank Ltd.", "INE171A01029"),
    ("FORTIS", "Fortis Healthcare Ltd.", "INE061F01013"),
    ("GAIL", "GAIL (India) Ltd.", "INE129A01019"),
    ("GLENMARK", "Glenmark Pharmaceuticals Ltd.", "INE935A01035"),
    ("GMRAIRPORT", "GMR Airports Ltd.", "INE776C01039"),
    ("GODFRYPHLP", "Godfrey Phillips India Ltd.", "INE260B01028"),
    ("GODREJCP", "Godrej Consumer Products Ltd.", "INE102D01028"),
    ("GODREJPROP", "Godrej Properties Ltd.", "INE484J01027"),
    ("GRASIM", "Grasim Industries Ltd.", "INE047A01021"),
    ("GROWW", "Billionbrains Garage Ventures Ltd.", "INE0HOQ01053"),
    ("GVT&D", "GE Vernova T&D India Ltd.", "INE200A01026"),
    ("HAL", "Hindustan Aeronautics Ltd.", "INE066F01020"),
    ("HAVELLS", "Havells India Ltd.", "INE176B01034"),
    ("HCLTECH", "HCL Technologies Ltd.", "INE860A01027"),
    ("HDFCAMC", "HDFC Asset Management Company Ltd.", "INE127D01025"),
    ("HDFCBANK", "HDFC Bank Ltd.", "INE040A01034"),
    ("HDFCLIFE", "HDFC Life Insurance Company Ltd.", "INE795G01014"),
    ("HEROMOTOCO", "Hero MotoCorp Ltd.", "INE158A01026"),
    ("HINDALCO", "Hindalco Industries Ltd.", "INE038A01020"),
    ("HINDPETRO", "Hindustan Petroleum Corporation Ltd.", "INE094A01015"),
    ("HINDUNILVR", "Hindustan Unilever Ltd.", "INE030A01027"),
    ("HINDZINC", "Hindustan Zinc Ltd.", "INE267A01025"),
    ("HUDCO", "Housing & Urban Development Corporation Ltd.", "INE031A01017"),
    ("HYUNDAI", "Hyundai Motor India Ltd.", "INE0V6F01027"),
    ("ICICIAMC", "ICICI Prudential Asset Management Company Ltd.", "INE346A01027"),
    ("ICICIBANK", "ICICI Bank Ltd.", "INE090A01021"),
    ("ICICIGI", "ICICI Lombard General Insurance Company Ltd.", "INE765G01017"),
    ("IDEA", "Vodafone Idea Ltd.", "INE669E01016"),
    ("IDFCFIRSTB", "IDFC First Bank Ltd.", "INE092T01019"),
    ("INDHOTEL", "Indian Hotels Co. Ltd.", "INE053A01029"),
    ("INDIANB", "Indian Bank", "INE562A01011"),
    ("INDIGO", "InterGlobe Aviation Ltd.", "INE646L01027"),
    ("INDUSINDBK", "IndusInd Bank Ltd.", "INE095A01012"),
    ("INDUSTOWER", "Indus Towers Ltd.", "INE121J01017"),
    ("INFY", "Infosys Ltd.", "INE009A01021"),
    ("IOC", "Indian Oil Corporation Ltd.", "INE242A01010"),
    ("IRCTC", "Indian Railway Catering And Tourism Corporation Ltd.", "INE335Y01020"),
    ("IREDA", "Indian Renewable Energy Development Agency Ltd.", "INE202E01016"),
    ("IRFC", "Indian Railway Finance Corporation Ltd.", "INE053F01010"),
    ("ITC", "ITC Ltd.", "INE154A01025"),
    ("JINDALSTEL", "Jindal Steel Ltd.", "INE749A01030"),
    ("JIOFIN", "Jio Financial Services Ltd.", "INE758E01017"),
    ("JSWENERGY", "JSW Energy Ltd.", "INE121E01018"),
    ("JSWSTEEL", "JSW Steel Ltd.", "INE019A01038"),
    ("JUBLFOOD", "Jubilant Foodworks Ltd.", "INE797F01020"),
    ("KALYANKJIL", "Kalyan Jewellers India Ltd.", "INE303R01014"),
    ("KEI", "KEI Industries Ltd.", "INE878B01027"),
    ("KOTAKBANK", "Kotak Mahindra Bank Ltd.", "INE237A01036"),
    ("KPITTECH", "KPIT Technologies Ltd.", "INE04I401011"),
    ("LAURUSLABS", "Laurus Labs Ltd.", "INE947Q01028"),
    ("LENSKART", "Lenskart Solutions Ltd.", "INE956O01016"),
    ("LGEINDIA", "LG Electronics India Ltd.", "INE324D01010"),
    ("LICHSGFIN", "LIC Housing Finance Ltd.", "INE115A01026"),
    ("LODHA", "Lodha Developers Ltd.", "INE670K01029"),
    ("LT", "Larsen & Toubro Ltd.", "INE018A01030"),
    ("LTF", "L&T Finance Ltd.", "INE498L01015"),
    ("LTM", "LTM Ltd.", "INE214T01019"),
    ("LUPIN", "Lupin Ltd.", "INE326A01037"),
    ("M&M", "Mahindra & Mahindra Ltd.", "INE101A01026"),
    ("M&MFIN", "Mahindra & Mahindra Financial Services Ltd.", "INE774D01024"),
    ("MANKIND", "Mankind Pharma Ltd.", "INE634S01028"),
    ("MARICO", "Marico Ltd.", "INE196A01026"),
    ("MARUTI", "Maruti Suzuki India Ltd.", "INE585B01010"),
    ("MAXHEALTH", "Max Healthcare Institute Ltd.", "INE027H01010"),
    ("MAZDOCK", "Mazagoan Dock Shipbuilders Ltd.", "INE249Z01020"),
    ("MCX", "Multi Commodity Exchange of India Ltd.", "INE745G01043"),
    ("MFSL", "Max Financial Services Ltd.", "INE180A01020"),
    ("MOTHERSON", "Samvardhana Motherson International Ltd.", "INE775A01035"),
    ("MOTILALOFS", "Motilal Oswal Financial Services Ltd.", "INE338I01027"),
    ("MPHASIS", "MphasiS Ltd.", "INE356A01018"),
    ("MRF", "MRF Ltd.", "INE883A01011"),
    ("MUTHOOTFIN", "Muthoot Finance Ltd.", "INE414G01012"),
    ("NATIONALUM", "National Aluminium Co. Ltd.", "INE139A01034"),
    ("NAUKRI", "Info Edge (India) Ltd.", "INE663F01032"),
    ("NESTLEIND", "Nestle India Ltd.", "INE239A01024"),
    ("NHPC", "NHPC Ltd.", "INE848E01016"),
    ("NMDC", "NMDC Ltd.", "INE584A01023"),
    ("NTPC", "NTPC Ltd.", "INE733E01010"),
    ("NYKAA", "FSN E-Commerce Ventures Ltd.", "INE388Y01029"),
    ("OBEROIRLTY", "Oberoi Realty Ltd.", "INE093I01010"),
    ("OFSS", "Oracle Financial Services Software Ltd.", "INE881D01027"),
    ("OIL", "Oil India Ltd.", "INE274J01014"),
    ("ONGC", "Oil & Natural Gas Corporation Ltd.", "INE213A01029"),
    ("PAGEIND", "Page Industries Ltd.", "INE761H01022"),
    ("PATANJALI", "Patanjali Foods Ltd.", "INE619A01035"),
    ("PAYTM", "One 97 Communications Ltd.", "INE982J01020"),
    ("PERSISTENT", "Persistent Systems Ltd.", "INE262H01021"),
    ("PFC", "Power Finance Corporation Ltd.", "INE134E01011"),
    ("PHOENIXLTD", "Phoenix Mills Ltd.", "INE211B01039"),
    ("PIDILITIND", "Pidilite Industries Ltd.", "INE318A01026"),
    ("PIIND", "PI Industries Ltd.", "INE603J01030"),
    ("PNB", "Punjab National Bank", "INE160A01022"),
    ("POLICYBZR", "PB Fintech Ltd.", "INE417T01026"),
    ("POLYCAB", "Polycab India Ltd.", "INE455K01017"),
    ("POWERGRID", "Power Grid Corporation of India Ltd.", "INE752E01010"),
    ("POWERINDIA", "Hitachi Energy India Ltd.", "INE07Y701011"),
    ("PREMIERENE", "Premier Energies Ltd.", "INE0BS701011"),
    ("PRESTIGE", "Prestige Estates Projects Ltd.", "INE811K01011"),
    ("RADICO", "Radico Khaitan Ltd", "INE944F01028"),
    ("RECLTD", "REC Ltd.", "INE020B01018"),
    ("RELIANCE", "Reliance Industries Ltd.", "INE002A01018"),
    ("RVNL", "Rail Vikas Nigam Ltd.", "INE415G01027"),
    ("SAIL", "Steel Authority of India Ltd.", "INE114A01011"),
    ("SBICARD", "SBI Cards and Payment Services Ltd.", "INE018E01016"),
    ("SBILIFE", "SBI Life Insurance Company Ltd.", "INE123W01016"),
    ("SBIN", "State Bank of India", "INE062A01020"),
    ("SHREECEM", "Shree Cement Ltd.", "INE070A01015"),
    ("SHRIRAMFIN", "Shriram Finance Ltd.", "INE721A01047"),
    ("SIEMENS", "Siemens Ltd.", "INE003A01024"),
    ("SOLARINDS", "Solar Industries India Ltd.", "INE343H01029"),
    ("SRF", "SRF Ltd.", "INE647A01010"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd.", "INE044A01036"),
    ("SUPREMEIND", "Supreme Industries Ltd.", "INE195A01028"),
    ("SUZLON", "Suzlon Energy Ltd.", "INE040H01021"),
    ("SWIGGY", "Swiggy Ltd.", "INE00H001014"),
    ("TATACAP", "Tata Capital Ltd.", "INE976I01016"),
    ("TATACOMM", "Tata Communications Ltd.", "INE151A01013"),
    ("TATACONSUM", "Tata Consumer Products Ltd.", "INE192A01025"),
    ("TATAELXSI", "Tata Elxsi Ltd.", "INE670A01012"),
    ("TATAINVEST", "Tata Investment Corporation Ltd.", "INE672A01026"),
    ("TATAPOWER", "Tata Power Co. Ltd.", "INE245A01021"),
    ("TATASTEEL", "Tata Steel Ltd.", "INE081A01020"),
    ("TCS", "Tata Consultancy Services Ltd.", "INE467B01029"),
    ("TECHM", "Tech Mahindra Ltd.", "INE669C01036"),
    ("TIINDIA", "Tube Investments of India Ltd.", "INE974X01010"),
    ("TITAN", "Titan Company Ltd.", "INE280A01028"),
    ("TMCV", "Tata Motors Ltd.", "INE1TAE01010"),
    ("TMPV", "Tata Motors Passenger Vehicles Ltd.", "INE155A01022"),
    ("TORNTPHARM", "Torrent Pharmaceuticals Ltd.", "INE685A01028"),
    ("TRENT", "Trent Ltd.", "INE849A01020"),
    ("TVSMOTOR", "TVS Motor Company Ltd.", "INE494B01023"),
    ("ULTRACEMCO", "UltraTech Cement Ltd.", "INE481G01011"),
    ("UNIONBANK", "Union Bank of India", "INE692A01016"),
    ("UNITDSPR", "United Spirits Ltd.", "INE854D01024"),
    ("UPL", "UPL Ltd.", "INE628A01036"),
    ("VBL", "Varun Beverages Ltd.", "INE200M01039"),
    ("VEDL", "Vedanta Ltd.", "INE205A01025"),
    ("VMM", "Vishal Mega Mart Ltd.", "INE01EA01019"),
    ("VOLTAS", "Voltas Ltd.", "INE226A01021"),
    ("WAAREEENER", "Waaree Energies Ltd.", "INE377N01017"),
    ("WIPRO", "Wipro Ltd.", "INE075A01022"),
    ("YESBANK", "Yes Bank Ltd.", "INE528G01035"),
    ("ZYDUSLIFE", "Zydus Lifesciences Ltd.", "INE010B01027"),
)

def _build_symbols() -> tuple[str, ...]:
    """All NIFTY 200 symbols, deterministically sorted and de-duplicated."""
    return tuple(sorted({row[0] for row in NIFTY200_CONSTITUENTS}))

#: All NIFTY 200 NSE trading symbols, sorted lexicographically (200 unique).
NIFTY200_SYMBOLS: tuple[str, ...] = _build_symbols()

#: {symbol: (company_name, isin)} lookup (deterministic).
NIFTY200_METADATA: dict[str, tuple[str, str]] = {
    row[0]: (row[1], row[2]) for row in NIFTY200_CONSTITUENTS
}

#: {symbol: isin} quick lookup (deterministic).
NIFTY200_ISINS: dict[str, str] = {row[0]: row[2] for row in NIFTY200_CONSTITUENTS}

__all__ = [
    "NIFTY200_CONSTITUENTS",
    "NIFTY200_CSV_SHA256",
    "NIFTY200_ISINS",
    "NIFTY200_MANIFEST_VERSION",
    "NIFTY200_METADATA",
    "NIFTY200_SOURCE_URL",
    "NIFTY200_SYMBOLS",
]
