import akshare as ak

target_codes = {
    "510050", "510310", "159352", "510880", "561280",
    "159957", "159949", "159991", "159780", "159811",
    "512480", "159560", "159516", "562820", "159590",
    "562920", "159819",
}

df = ak.fund_etf_spot_em()
result = df[df["代码"].isin(target_codes)][["代码", "名称"]].reset_index(drop=True)

for _, row in result.iterrows():
    print(f"{row['代码']}  {row['名称']}")

found = set(result["代码"])
missing = target_codes - found
if missing:
    print(f"\n未找到: {sorted(missing)}")