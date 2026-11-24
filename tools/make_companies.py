"""Builds companies.tsv: the public companies the app recognises in titles, with what each one is.

Sources (run again to refresh): the S&P 500, S&P 1500 (S&P 500 + MidCap 400 + SmallCap 600) and Russell 1000
constituent lists with their GICS sub-industry, as CSV files with columns Symbol / Security or Company /
GICS Sector / GICS Sub-Industry; plus the hand-written lines in COMPANY_OVERRIDES below (companies outside those
indexes, and plainer descriptions for well-known names).

    python tools/make_companies.py sp500.csv sp1500.csv combined.csv russell1000.csv

Needs: pip install wordfreq (only here, to spot company names that are everyday words; the app doesn't need it).
"""
import csv
import os
import re
import sys

# GICS sub-industry -> what to call such a company in a title
SUB_INDUSTRY = {
    "Advertising": "advertising company", "Aerospace & Defense": "aerospace and defense company",
    "Agricultural & Farm Machinery": "farm machinery maker",
    "Agricultural Products & Services": "agricultural products company", "Air Freight & Logistics": "logistics company",
    "Airlines": "airline", "Alternative Carriers": "telecommunications company", "Aluminum": "aluminum producer",
    "Apparel Retail": "clothing retailer", "Apparel, Accessories & Luxury Goods": "apparel and accessories company",
    "Application Software": "software company", "Asset Management & Custody Banks": "asset management firm",
    "Auto Parts & Equipment": "auto parts maker", "Automobile Manufacturers": "automaker",
    "Automotive Parts & Equipment": "auto parts maker", "Automotive Retail": "automotive retailer",
    "Biotechnology": "biotechnology company", "Brewers": "brewing company", "Broadcasting": "broadcasting company",
    "Broadline Retail": "retailer", "Building Products": "building products company",
    "Cable & Satellite": "cable and broadband company", "Cargo Ground Transportation": "trucking and freight company",
    "Casinos & Gaming": "casino and gaming company", "Coal & Consumable Fuels": "coal and fuels company",
    "Commercial & Residential Mortgage Finance": "mortgage finance company",
    "Commercial Printing": "commercial printing company", "Commodity Chemicals": "chemical company",
    "Communications Equipment": "networking and communications equipment company",
    "Computer & Electronics Retail": "electronics retailer", "Construction & Engineering": "construction and engineering company",
    "Construction Machinery & Heavy Transportation Equipment": "heavy machinery maker",
    "Construction Machinery & Heavy Trucks": "heavy machinery and truck maker",
    "Construction Materials": "construction materials company", "Consumer Electronics": "consumer electronics company",
    "Consumer Finance": "consumer finance company", "Consumer Staples Merchandise Retail": "retailer",
    "Copper": "copper mining company", "Data Center REITs": "data center real estate investment trust",
    "Data Processing & Outsourced Services": "data processing and business services company",
    "Distillers & Vintners": "spirits and wine company", "Distributors": "distribution company",
    "Diversified Banks": "bank", "Diversified Capital Markets": "financial services company",
    "Diversified Chemicals": "chemical company", "Diversified Financial Services": "financial services company",
    "Diversified Metals & Mining": "mining company", "Diversified REITs": "real estate investment trust",
    "Diversified Support Services": "business services company", "Drug Retail": "pharmacy chain",
    "Education Services": "education company", "Electric Utilities": "electric utility",
    "Electrical Components & Equipment": "electrical equipment company", "Electronic Components": "electronic components maker",
    "Electronic Equipment & Instruments": "electronic instruments company",
    "Electronic Manufacturing Services": "electronics manufacturing company",
    "Environmental & Facilities Services": "environmental and facilities services company",
    "Fertilizers & Agricultural Chemicals": "fertilizer and agricultural chemicals company",
    "Financial Exchanges & Data": "financial exchange and market data company", "Food Distributors": "food distributor",
    "Food Retail": "grocery retailer", "Footwear": "footwear company", "Forest Products": "forest products company",
    "Gas Utilities": "natural gas utility", "Gold": "gold mining company", "Health Care Distributors": "health care distributor",
    "Health Care Equipment": "medical device company", "Health Care Facilities": "hospital and health care facilities operator",
    "Health Care REITs": "health care real estate investment trust", "Health Care Services": "health care services company",
    "Health Care Supplies": "medical supplies company", "Health Care Technology": "health care technology company",
    "Heavy Electrical Equipment": "heavy electrical equipment company", "Home Furnishings": "home furnishings company",
    "Home Improvement Retail": "home improvement retailer", "Homebuilding": "homebuilder",
    "Homefurnishing Retail": "home furnishings retailer", "Hotel & Resort REITs": "hotel and resort real estate investment trust",
    "Hotels, Resorts & Cruise Lines": "hotel and travel company", "Household Appliances": "household appliance maker",
    "Household Products": "household products company", "Housewares & Specialties": "housewares company",
    "Human Resource & Employment Services": "human resources and staffing company",
    "Hypermarkets & Super Centers": "retailer", "IT Consulting & Other Services": "IT consulting and services company",
    "Independent Power Producers & Energy Traders": "power generation company",
    "Industrial Conglomerates": "industrial conglomerate", "Industrial Gases": "industrial gases company",
    "Industrial Machinery": "industrial machinery company",
    "Industrial Machinery & Supplies & Components": "industrial machinery company",
    "Industrial REITs": "industrial real estate investment trust", "Insurance Brokers": "insurance brokerage",
    "Integrated Oil & Gas": "oil and gas company", "Integrated Telecommunication Services": "telecommunications company",
    "Interactive Home Entertainment": "video game company", "Interactive Media & Services": "internet media company",
    "Internet Services & Infrastructure": "internet services and infrastructure company",
    "Investment Banking & Brokerage": "investment bank and brokerage", "Leisure Facilities": "leisure and entertainment venues company",
    "Leisure Products": "leisure products company", "Life & Health Insurance": "life and health insurance company",
    "Life Sciences Tools & Services": "life sciences tools company", "Managed Health Care": "health insurance company",
    "Marine": "shipping company", "Metal & Glass Containers": "packaging company",
    "Metal, Glass & Plastic Containers": "packaging company", "Mortgage REITs": "mortgage real estate investment trust",
    "Motorcycle Manufacturers": "motorcycle maker", "Movies & Entertainment": "entertainment company",
    "Multi-Family Residential REITs": "apartment real estate investment trust",
    "Multi-Sector Holdings": "holding company", "Multi-Utilities": "utility company",
    "Multi-line Insurance": "insurance company", "Office REITs": "office real estate investment trust",
    "Office Services & Supplies": "office supplies company", "Oil & Gas Drilling": "oil and gas drilling company",
    "Oil & Gas Equipment & Services": "oilfield services company",
    "Oil & Gas Exploration & Production": "oil and gas exploration and production company",
    "Oil & Gas Refining & Marketing": "oil refining company", "Oil & Gas Storage & Transportation": "oil and gas pipeline company",
    "Other Specialized REITs": "real estate investment trust", "Other Specialty Retail": "specialty retailer",
    "Packaged Foods & Meats": "packaged food company", "Paper & Plastic Packaging Products & Materials": "packaging company",
    "Paper Products": "paper products company", "Passenger Airlines": "airline",
    "Passenger Ground Transportation": "ride-hailing and transportation company",
    "Personal Care Products": "personal care products company", "Personal Products": "personal care products company",
    "Pharmaceuticals": "pharmaceutical company", "Property & Casualty Insurance": "property and casualty insurance company",
    "Publishing": "publishing company", "Rail Transportation": "railroad", "Real Estate Development": "real estate developer",
    "Real Estate Services": "real estate services company", "Regional Banks": "regional bank", "Reinsurance": "reinsurance company",
    "Renewable Electricity": "renewable energy company", "Research & Consulting Services": "research and consulting firm",
    "Restaurants": "restaurant company", "Retail REITs": "retail real estate investment trust",
    "Security & Alarm Services": "security and alarm company", "Self-Storage REITs": "self-storage real estate investment trust",
    "Semiconductor Equipment": "semiconductor equipment maker",
    "Semiconductor Materials & Equipment": "semiconductor equipment maker", "Semiconductors": "semiconductor company",
    "Silver": "silver mining company", "Single-Family Residential REITs": "rental home real estate investment trust",
    "Soft Drinks": "beverage company", "Soft Drinks & Non-alcoholic Beverages": "beverage company",
    "Specialized Consumer Services": "consumer services company", "Specialized Finance": "specialty finance company",
    "Specialized REITs": "real estate investment trust", "Specialty Chemicals": "specialty chemicals company",
    "Specialty Stores": "specialty retailer", "Steel": "steel producer", "Systems Software": "software company",
    "Technology Distributors": "technology distributor",
    "Technology Hardware, Storage & Peripherals": "computer hardware and technology company",
    "Telecom Tower REITs": "cell tower real estate investment trust", "Timber REITs": "timberland real estate investment trust",
    "Tires & Rubber": "tire maker", "Tobacco": "tobacco company", "Trading Companies & Distributors": "industrial distributor",
    "Transaction & Payment Processing Services": "payments company", "Water Utilities": "water utility",
    "Wireless Telecommunication Services": "wireless carrier",
}

# Plainer or fuller descriptions for well-known names, and companies outside the index lists.
# name | other names, comma-separated (optional) | what it is | ticker (optional)
COMPANY_OVERRIDES = """\
Akamai Technologies | Akamai | cloud computing, cybersecurity and content delivery company | AKAM
Automatic Data Processing | ADP | payroll and human resources software company | ADP
Airbnb | | online vacation rental marketplace | ABNB
Apple | | consumer electronics and technology company | AAPL
Amazon | | e-commerce and cloud computing company | AMZN
Alphabet | Google | technology company and parent of Google | GOOGL
Meta Platforms | Meta, Facebook | social media and technology company | META
Microsoft | | software and cloud computing company | MSFT
Nvidia | NVIDIA | graphics and AI chip maker | NVDA
Advanced Micro Devices | AMD | semiconductor company making processors and graphics chips | AMD
Adobe | | creative and design software company | ADBE
Autodesk | | design and engineering software company | ADSK
Accenture | | IT consulting and professional services firm | ACN
American International Group | AIG | multinational insurance company | AIG
AES Corporation | AES | power generation and utility company | AES
Ameren | | electric and natural gas utility | AEE
American Electric Power | AEP | electric utility | AEP
Abbott Laboratories | Abbott | medical device and health care company | ABT
AbbVie | | biopharmaceutical company | ABBV
Agilent Technologies | Agilent | life sciences and diagnostics company | A
Albemarle | | lithium and specialty chemicals company | ALB
Align Technology | Align, Invisalign | maker of Invisalign clear aligners | ALGN
Allegion | | security products and locks company | ALLE
Allstate | | insurance company | ALL
Analog Devices | | semiconductor company | ADI
Applied Materials | | semiconductor equipment maker | AMAT
AppLovin | | mobile app marketing and advertising technology company | APP
Aptiv | APTIV | automotive technology and parts company | APTV
Arista Networks | Arista | cloud networking company | ANET
Arch Capital Group | Arch Capital | insurance and reinsurance company | ACGL
Ameriprise Financial | Ameriprise | financial planning and wealth management company | AMP
Ametek | AMETEK | electronic instruments and electromechanical devices maker | AME
Amphenol | | electronic connector and sensor maker | APH
Aon | AON | professional services and insurance brokerage firm | AON
APA Corporation | APA | oil and gas exploration company | APA
Apollo Global Management | Apollo | private equity and asset management firm | APO
Air Products | Air Products and Chemicals | industrial gases company | APD
A. O. Smith | AO Smith | water heater and boiler maker | AOS
Abercrombie & Fitch | Abercrombie and Fitch, Abercrombie | clothing retailer | ANF
Albertsons Companies | Albertsons | supermarket chain | ACI
AutoNation | | automotive retailer | AN
Arrow Electronics | | electronic components and technology distributor | ARW
Antero Midstream | | natural gas pipeline and midstream company | AM
Amkor Technology | Amkor | semiconductor packaging and test services company | AMKR
Allegro MicroSystems | Allegro Microsystems | semiconductor company | ALGM
Astera Labs | | semiconductor connectivity company for AI and cloud data centers | ALAB
ACM Research | | semiconductor equipment maker | ACMR
AGNC Investment | AGNC | mortgage real estate investment trust | AGNC
Ashland | | specialty chemicals company | ASH
ADT | | home security company | ADT
Affirm | Affirm Holdings | buy now, pay later financial technology company | AFRM
Ambev | AmBev | Brazilian brewing company | ABEV
Almonty Industries | Almonty | tungsten mining company | ALM
Alnylam Pharmaceuticals | Alnylam | RNA interference biotechnology company | ALNY
AMC Entertainment | AMC Theatres, AMC | movie theater chain | AMC
Arm Holdings | | chip design company | ARM
ASML | | semiconductor lithography equipment maker | ASML
Anthropic | | artificial intelligence safety and research company |
OpenAI | | artificial intelligence research company |
American Airlines | | airline | AAL
Costco Wholesale | Costco | warehouse club retailer | COST
Samsung Electronics | Samsung | South Korean electronics company | SSNLF
Sony | Sony Group | Japanese electronics and entertainment company | SONY
Toyota Motor | Toyota | Japanese automaker | TM
Honda Motor | Honda | Japanese automaker and motorcycle maker | HMC
Nissan Motor | Nissan | Japanese automaker | NSANY
Nintendo | | Japanese video game company | NTDOY
LG Electronics | LG | South Korean electronics company |
Taiwan Semiconductor Manufacturing | TSMC | Taiwanese semiconductor foundry | TSM
Alibaba Group | Alibaba | Chinese e-commerce and technology company | BABA
Tencent | | Chinese technology and video game company | TCEHY
Nestlé | Nestle | Swiss food and beverage company | NSRGY
Unilever | | British consumer goods company | UL
Shell | | British oil and gas company | SHEL
BP | | British oil and gas company | BP
Siemens | | German industrial technology company | SIEGY
SAP | | German enterprise software company | SAP
Volkswagen | VW | German automaker | VWAGY
BMW | | German luxury automaker | BMWYY
Mercedes-Benz Group | Mercedes-Benz, Mercedes | German luxury automaker | MBGYY
Spotify | | music streaming company | SPOT
Shopify | | e-commerce software company | SHOP
Adidas | | German sportswear company | ADDYY
LVMH | Louis Vuitton | French luxury goods company | LVMUY
Novo Nordisk | | Danish pharmaceutical company | NVO
AstraZeneca | | British-Swedish pharmaceutical company | AZN
Toyota Industries | | Japanese industrial machinery company |
Rivian | Rivian Automotive | electric vehicle maker | RIVN
Lucid Group | Lucid Motors, Lucid | electric vehicle maker | LCID
SpaceX | | rocket and satellite company |
Stripe | | online payments company |
Walmart | | retail corporation | WMT
Coca-Cola Company | Coca-Cola | beverage company | KO
"""

SUFFIXES = re.compile(r"(?:,?\s+(?:Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.|plc|PLC|Ltd\.?|Limited|N\.V\.|"
                      r"S\.A\.|SA|AG|SE|LLC|L\.P\.|Holdings?|Group|Companies|& Co\.?))+$")
CONTEXT_FREE_MAX = 2.9     # names that are English words (caterpillar, adobe, target) need a "Logo", "Sign"… in the title


def display_name(name: str) -> str:
    name = re.sub(r"\s*\(Class [A-Z]\)", "", name).strip()
    the = name.endswith("(The)")
    name = name.replace("(The)", "").strip()
    name = re.sub(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|plc|PLC|Ltd\.?|Limited|N\.V\.|S\.A\.)$", "", name)
    return ("The " if the and not name.startswith("The ") else "") + name


def core_name(name: str) -> str:
    n = re.sub(r"^The\s+", "", display_name(name))
    while True:
        m = SUFFIXES.sub("", n).strip(" ,")
        if m == n or not m:
            return n
        n = m


def article(phrase: str) -> str:
    w = phrase.split()[0]
    vowel = w[0].lower() in "aeio" or w in ("IT", "HR", "RNA", "SEC", "MRI", "LED", "LCD", "x-ray") or \
        (w[0].lower() == "u" and not re.match(r"u(?:ni|ti|s[aeu])", w.lower())) or w.lower().startswith("hour")
    return ("an " if vowel else "a ") + phrase


def main(paths):
    from wordfreq import zipf_frequency
    rows = {}
    for p in paths:
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                sym = (r.get("Symbol") or "").strip()
                name = (r.get("Security") or r.get("Company") or "").strip()
                sub = (r.get("GICS Sub-Industry") or "").strip()
                if sym and name and sub and sym not in rows:
                    rows[sym] = (display_name(name), SUB_INDUSTRY.get(sub, sub.lower() + " company"))
    out = {}                                                  # display name -> [aliases, what, ticker]
    for sym, (disp, what) in rows.items():
        out.setdefault(disp, [set(), what, sym])
    for line in COMPANY_OVERRIDES.splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split("|")]
        disp, others, what = parts[0], parts[1], parts[2]
        sym = parts[3] if len(parts) > 3 else ""
        # an override replaces the index line for the same ticker (Akamai Technologies, Inc. -> Akamai Technologies)
        for d in [d for d, v in out.items() if sym and v[2] == sym and d != disp]:
            out.pop(d)
        aliases = {a.strip() for a in others.split(",") if a.strip()}
        out[disp] = [aliases, what, sym]
    lines = ["# name\tother names (~ = an everyday word: only with Logo, Sign, Store… after it; $ = the ticker, in "
             "capitals)\twhat it is\tticker"]
    first_words = {}
    for disp in out:
        w = core_name(disp).split()[0]
        first_words[w] = first_words.get(w, 0) + 1
    common = lambda n: zipf_frequency(re.sub(r"[^a-z' ]", "", n.lower()), "en")
    for disp, (aliases, what, sym) in sorted(out.items(), key=lambda kv: kv[0].casefold()):
        core = core_name(disp)
        names = {disp, core} | aliases
        # the first word alone, when it's distinctive (Akamai, Albemarle) and no other company starts with it
        first = core.split()[0]
        if " " in core and first_words.get(first, 0) == 1 and len(first) >= 4 and common(first) < 3.0 \
                and not first.isupper():
            names.add(first)
        marked = []
        for n in sorted({n for n in names if n}, key=len, reverse=True):
            if n == disp and " " in n:
                continue                                      # (the name itself is always matched)
            single = " " not in n
            if single and n.isupper() and len(n) <= 5:        # AIG, ADP: the letters, in capitals
                marked.append(("$~" if common(n) >= CONTEXT_FREE_MAX else "$") + n)
            elif single and common(n) >= CONTEXT_FREE_MAX:   # Apple, Target, Arch: needs "Logo", "Store"…
                marked.append("~" + n)
            elif n != disp:
                marked.append(n)
        if " " not in disp:                                    # a one-word name: mark it too
            if disp.isupper() and len(disp) <= 5:
                marked.insert(0, ("$~" if common(disp) >= CONTEXT_FREE_MAX else "$") + disp)
            elif common(disp) >= CONTEXT_FREE_MAX:
                marked.insert(0, "~" + disp)
            else:
                marked.insert(0, disp)
        if sym and sym.isalpha() and 3 <= len(sym) <= 5 and sym not in {m.lstrip("$~") for m in marked}:
            marked.append(("$" if common(sym) < 3.0 else "$~") + sym)     # ARM, CAT: only with Logo, Stock…
        lines.append("\t".join([disp, "; ".join(dict.fromkeys(marked)), what, sym]))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "companies.tsv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{len(lines) - 1} companies written to companies.tsv")


if __name__ == "__main__":
    main(sys.argv[1:])
