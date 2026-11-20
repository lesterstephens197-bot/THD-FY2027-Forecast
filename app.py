
import io, calendar
from datetime import date
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Waykar FY2027 Forecast", layout="wide")
st.title("Waykar FY2027 Sales Forecast Workbench")
st.caption("The Home Depot | DF + VDF | SKU-level and monthly planning")

MONTHS = [calendar.month_abbr[i] for i in range(1, 13)]

def read_file(f):
    if f is None: return None
    if f.name.lower().endswith(".csv"):
        try: return pd.read_csv(f, encoding="utf-8-sig")
        except UnicodeDecodeError:
            f.seek(0); return pd.read_csv(f, encoding="gb18030")
    return pd.read_excel(f)

def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False).str.strip(), errors="coerce").fillna(0)

def normalize(df, channel, date=None, sku=None, qty=None, amount=None, name=None, position=None):
    date_col, sku_col, qty_col, amount_col = date, sku, qty, amount
    name_col, position_col = name, position
    if df is None: return pd.DataFrame()
    x = pd.DataFrame()
    x["Channel"] = channel
    x["Date"] = pd.to_datetime(df[date_col], errors="coerce")
    x["SKU"] = df[sku_col].astype(str).str.strip().str.upper()
    x["Units"] = num(df[qty_col])
    x["Amount"] = num(df[amount_col])
    x["Product"] = df[name_col].fillna("").astype(str) if name_col else ""
    x["Position"] = df[position_col].fillna("").astype(str) if position_col else ""
    x = x.dropna(subset=["Date"])
    x = x[(x["SKU"] != "") & (x["SKU"] != "NAN")]
    x["Month"] = x["Date"].dt.month
    return x

def alloc(total, weights):
    total = max(0, int(round(float(total))))
    w = np.array(weights, dtype=float)
    w = np.where(np.isfinite(w) & (w > 0), w, 0)
    if w.sum() == 0: w = np.ones(12)
    raw = total * w / w.sum()
    a = np.floor(raw).astype(int)
    for i in np.argsort(-(raw-a))[:total-a.sum()]: a[i] += 1
    return a

def export_excel(sheets):
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            ws.freeze_panes = "B2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(max(len(str(c.value or "")) for c in col)+2, 12), 28)
    b.seek(0)
    return b.getvalue()

st.sidebar.header("上传历史数据")
vdf_file = st.sidebar.file_uploader("VDF数据 Excel/CSV", type=["xlsx","xls","csv"])
df_file = st.sidebar.file_uploader("DF数据 Excel/CSV", type=["xlsx","xls","csv"])
vdf = read_file(vdf_file)
df = read_file(df_file)

if vdf is None and df is None:
    st.info("请上传2026年VDF和/或DF销售数据。")
    st.markdown("**VDF字段：** Order Date、产品SKU、Quantity、Total Cost。  \n**DF字段：** 订单创建日期、产品SKU、下单数量、总销售额。")
    st.stop()

def choose(label, data, default, key, required=True):
    opts = list(data.columns)
    if not opts: return None
    idx = opts.index(default) if default in opts else 0
    if not required:
        opts = ["（不使用）"] + opts
        idx = opts.index(default) + 1 if default in data.columns else 0
    val = st.selectbox(label, opts, index=idx, key=key)
    return None if val == "（不使用）" else val

parts = []
with st.expander("字段映射（如默认表头不一致，可在这里修改）", expanded=False):
    if vdf is not None:
        st.markdown("**VDF字段映射**")
        vdc = {
            "date": choose("VDF日期", vdf, "Order Date", "vdate"),
            "sku": choose("VDF产品SKU", vdf, "产品SKU", "vsku"),
            "qty": choose("VDF数量", vdf, "Quantity", "vqty"),
            "amount": choose("VDF金额", vdf, "Total Cost", "vamt"),
            "name": choose("VDF产品名称", vdf, "产品名称", "vname", False),
            "position": choose("VDF产品定位", vdf, "产品定位", "vpos", False),
        }
    if df is not None:
        st.markdown("**DF字段映射**")
        dc = {
            "date": choose("DF订单日期", df, "订单创建日期", "ddate"),
            "sku": choose("DF产品SKU", df, "产品SKU", "dsku"),
            "qty": choose("DF数量", df, "下单数量", "dqty"),
            "amount": choose("DF金额", df, "总销售额", "damt"),
            "name": choose("DF产品描述", df, "产品描述", "dname", False),
            "position": choose("DF产品定位", df, "产品定位", "dpos", False),
        }

if vdf is not None:
    if "vdc" not in locals():
        vdc = {"date":"Order Date","sku":"产品SKU","qty":"Quantity","amount":"Total Cost","name":"产品名称" if "产品名称" in vdf else None,"position":"产品定位" if "产品定位" in vdf else None}
    parts.append(normalize(vdf, "VDF", **vdc))
if df is not None:
    if "dc" not in locals():
        dc = {"date":"订单创建日期","sku":"产品SKU","qty":"下单数量","amount":"总销售额","name":"产品描述" if "产品描述" in df else None,"position":"产品定位" if "产品定位" in df else None}
    parts.append(normalize(df, "DF", **dc))

if not parts or any(p.empty for p in parts):
    st.error("数据为空或字段映射不完整。请检查日期、SKU、数量和金额字段。")
    st.stop()

raw = pd.concat(parts, ignore_index=True)
st.warning("口径确认：VDF Total Cost 与 DF 总销售额可能不是相同收入定义；DF 下单数量可能是发往DFC的PO数量，而非消费者POS销量。系统分渠道保留数据，合计金额暂称“来源金额合计”，请确认口径后再作为GMV。")

st.sidebar.header("历史统计区间")
hist_start = st.sidebar.date_input("开始日期", date(2026,1,1))
hist_end = st.sidebar.date_input("结束日期", date(2026,12,31))
hist = raw[(raw.Date >= pd.Timestamp(hist_start)) & (raw.Date < pd.Timestamp(hist_end) + pd.Timedelta(days=1))]
if hist.empty:
    st.error("所选日期区间没有有效记录。")
    st.stop()

st.sidebar.header("预测参数")
forecast_year = st.sidebar.number_input("预测年份", 2027, 2035, 2027)
default_growth = st.sidebar.number_input("默认SKU销量增长率（%）", -100.0, 500.0, 15.0, 5.0)
default_asp_change = st.sidebar.number_input("默认ASP变化率（%）", -80.0, 200.0, 0.0, 2.0)

# Historical rollup by channel/SKU.
sku_hist = hist.groupby(["Channel","SKU"], as_index=False).agg(
    Hist_Units=("Units","sum"), Hist_Amount=("Amount","sum"),
    Product=("Product", lambda s: next((v for v in s if v), "")),
    Position=("Position", lambda s: next((v for v in s if v), "")))
sku_hist["Hist_ASP"] = np.where(sku_hist.Hist_Units != 0, sku_hist.Hist_Amount/sku_hist.Hist_Units, 0)
monthly_hist = hist.groupby(["Channel","SKU","Month"], as_index=False).agg(Units=("Units","sum"), Amount=("Amount","sum"))

st.header("2026历史销售基准")
a,b,c,d = st.columns(4)
a.metric("记录行数", f"{len(hist):,}")
b.metric("历史数量合计", f"{hist.Units.sum():,.0f}")
c.metric("VDF来源金额", f"${hist.loc[hist.Channel=='VDF','Amount'].sum():,.2f}")
d.metric("DF来源金额", f"${hist.loc[hist.Channel=='DF','Amount'].sum():,.2f}")
hist_channel = hist.groupby("Channel", as_index=False).agg(Units=("Units","sum"), Amount=("Amount","sum"))
st.dataframe(hist_channel, use_container_width=True, hide_index=True)
st.subheader("历史SKU表现")
hist_display = sku_hist.sort_values("Hist_Amount", ascending=False).copy()
st.dataframe(hist_display, use_container_width=True, hide_index=True)

st.header("2027 SKU年度目标设置")
st.caption("可修改增长率、ASP变化率、年度销量手动目标、ASP手动值及是否纳入预测。新品可在下方新增。")
ass = sku_hist.copy()
ass["Growth %"] = default_growth
ass["ASP Change %"] = default_asp_change
ass["Annual Units Override"] = np.nan
ass["ASP Override"] = np.nan
ass["Include"] = True
ass["Allocation"] = "历史季节性"

if "new_sku_rows" not in st.session_state:
    st.session_state["new_sku_rows"] = []

with st.expander("新增2027新品SKU", expanded=False):
    with st.form("new_sku_form"):
        new_channel = st.selectbox("业务渠道", ["VDF","DF"])
        new_sku = st.text_input("产品SKU")
        new_product = st.text_input("产品名称")
        new_position = st.text_input("产品定位", value="New")
        new_target = st.number_input("年度销量目标", min_value=0, value=0, step=100)
        new_asp = st.number_input("预计ASP（USD）", min_value=0.0, value=0.0, step=10.0)
        submitted = st.form_submit_button("添加新品")
    if submitted and new_sku.strip():
        st.session_state["new_sku_rows"].append({
            "Channel":new_channel, "SKU":new_sku.strip().upper(), "Hist_Units":0,
            "Hist_Amount":0, "Product":new_product, "Position":new_position,
            "Hist_ASP":0, "Growth %":default_growth, "ASP Change %":default_asp_change,
            "Annual Units Override":new_target, "ASP Override":new_asp,
            "Include":True, "Allocation":"自定义月度权重"})
        st.success("新品已加入预测清单。")

if st.session_state["new_sku_rows"]:
    ass = pd.concat([ass, pd.DataFrame(st.session_state["new_sku_rows"])], ignore_index=True)

edit_cols = ["Channel","SKU","Product","Position","Hist_Units","Hist_Amount","Hist_ASP","Growth %","ASP Change %","Annual Units Override","ASP Override","Include","Allocation"]
edited = st.data_editor(
    ass[edit_cols], use_container_width=True, hide_index=True, num_rows="dynamic",
    column_config={
        "Hist_Units":st.column_config.NumberColumn("2026历史数量", disabled=True),
        "Hist_Amount":st.column_config.NumberColumn("2026历史金额", disabled=True, format="$%.2f"),
        "Hist_ASP":st.column_config.NumberColumn("历史ASP", disabled=True, format="$%.2f"),
        "Growth %":st.column_config.NumberColumn("销量增长率%", format="%.1f"),
        "ASP Change %":st.column_config.NumberColumn("ASP变化率%", format="%.1f"),
        "Annual Units Override":st.column_config.NumberColumn("年度销量手动目标", format="%.0f"),
        "ASP Override":st.column_config.NumberColumn("ASP手动值", format="$%.2f"),
        "Include":st.column_config.CheckboxColumn("纳入预测"),
        "Allocation":st.column_config.SelectboxColumn("月度分配", options=["历史季节性","平均分配","自定义月度权重"]),
    }, key="sku_assumptions")

st.header("月度季节性权重")
st.caption("权重仅用于“自定义月度权重”方式；历史季节性方式直接使用该SKU历史月度分布。")
base_month = hist.groupby("Month").Units.sum().reindex(range(1,13), fill_value=0).astype(float)
if base_month.sum() == 0: base_month[:] = 1
weights0 = base_month/base_month.sum()*100
weight_df = pd.DataFrame({"Month":MONTHS, "Weight %":weights0.values})
weight_edit = st.data_editor(weight_df, use_container_width=True, hide_index=True, key="weights",
    column_config={"Month":st.column_config.TextColumn(disabled=True),"Weight %":st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format="%.2f")})
weights = pd.to_numeric(weight_edit["Weight %"], errors="coerce").fillna(0).values
if weights.sum() == 0: weights = np.ones(12)/12
else: weights = weights/weights.sum()

# Generate annual forecast by channel + SKU.
forecast_rows = []
for _, r in edited.iterrows():
    if not bool(r.get("Include", True)): continue
    sku = str(r.get("SKU","")).strip().upper()
    channel = str(r.get("Channel","")).strip()
    if not sku or sku == "NAN": continue
    hist_units = float(pd.to_numeric(r.get("Hist_Units",0), errors="coerce") or 0)
    growth = float(pd.to_numeric(r.get("Growth %",default_growth), errors="coerce") or 0)
    manual_units = pd.to_numeric(r.get("Annual Units Override",np.nan), errors="coerce")
    annual_units = max(0, int(round(manual_units if pd.notna(manual_units) else hist_units*(1+growth/100))))
    hist_asp = float(pd.to_numeric(r.get("Hist_ASP",0), errors="coerce") or 0)
    asp_change = float(pd.to_numeric(r.get("ASP Change %",default_asp_change), errors="coerce") or 0)
    manual_asp = pd.to_numeric(r.get("ASP Override",np.nan), errors="coerce")
    asp = max(0, float(manual_asp if pd.notna(manual_asp) else hist_asp*(1+asp_change/100)))
    method = r.get("Allocation","历史季节性")
    h = monthly_hist[(monthly_hist.Channel==channel)&(monthly_hist.SKU==sku)].set_index("Month").Units.reindex(range(1,13), fill_value=0).values
    if method == "平均分配": w = np.ones(12)/12
    elif method == "历史季节性" and h.sum()>0: w = h/h.sum()
    else: w = weights
    monthly_units = alloc(annual_units,w)
    for m,u in enumerate(monthly_units, start=1):
        forecast_rows.append({"Year":int(forecast_year),"Channel":channel,"SKU":sku,"Product":r.get("Product",""),
            "Position":r.get("Position",""),"Month":MONTHS[m-1],"Month Number":m,
            "Forecast Units":int(u),"Forecast ASP":round(asp,2),"Forecast Sales":round(int(u)*asp,2),
            "Annual SKU Units":annual_units,"Growth %":growth,"ASP Change %":asp_change,"Allocation":method})

forecast = pd.DataFrame(forecast_rows)
if forecast.empty:
    st.warning("没有纳入预测的SKU。")
    st.stop()

st.header("月度SKU销量目标（可直接编辑）")
grid = forecast.pivot_table(index=["Channel","SKU","Product","Forecast ASP"], columns="Month", values="Forecast Units", aggfunc="sum", fill_value=0).reset_index()
for m in MONTHS:
    if m not in grid.columns: grid[m]=0
grid = grid[["Channel","SKU","Product","Forecast ASP"]+MONTHS]
grid_edit = st.data_editor(grid, use_container_width=True, hide_index=True, key="monthly_forecast_edit",
    column_config={m:st.column_config.NumberColumn(m,min_value=0,step=1,format="%.0f") for m in MONTHS})
meta = forecast.drop_duplicates(["Channel","SKU"]).set_index(["Channel","SKU"])
final_rows=[]
for _, r in grid_edit.iterrows():
    ch, sku = r["Channel"], r["SKU"]
    asp=float(r["Forecast ASP"])
    for m in MONTHS:
        u=max(0,int(round(float(pd.to_numeric(r[m],errors="coerce") or 0))))
        info=meta.loc[(ch,sku)]
        final_rows.append({"Year":int(forecast_year),"Channel":ch,"SKU":sku,"Product":r["Product"],
            "Month":m,"Forecast Units":u,"Forecast ASP":asp,"Forecast Sales":round(u*asp,2),
            "Position":info["Position"],"Growth %":info["Growth %"],"Allocation":info["Allocation"]})
final=pd.DataFrame(final_rows)

monthly=final.groupby("Month",as_index=False).agg(Units=("Forecast Units","sum"),Sales=("Forecast Sales","sum"))
channel=final.groupby("Channel",as_index=False).agg(Units=("Forecast Units","sum"),Sales=("Forecast Sales","sum"))
sku=final.groupby(["Channel","SKU","Product"],as_index=False).agg(Annual_Units=("Forecast Units","sum"),Annual_Sales=("Forecast Sales","sum")).sort_values("Annual_Sales",ascending=False)
total_units=int(final["Forecast Units"].sum())
total_sales=float(final["Forecast Sales"].sum())
asp_total=total_sales/total_units if total_units else 0

st.divider()
st.header(f"FY{forecast_year}预测结果")
k1,k2,k3,k4=st.columns(4)
k1.metric("年度预测销量",f"{total_units:,.0f} 台")
k2.metric("年度预测销售额",f"${total_sales:,.2f}")
k3.metric("预测ASP",f"${asp_total:,.2f}")
k4.metric("SKU/渠道组合数",f"{final[['Channel','SKU']].drop_duplicates().shape[0]:,}")
st.subheader("月度销售额目标")
st.bar_chart(monthly.set_index("Month")[["Sales"]])
st.dataframe(monthly.style.format({"Sales":"${:,.2f}","Units":"{:,.0f}"}),use_container_width=True,hide_index=True)
st.subheader("DF / VDF目标拆分")
st.dataframe(channel.style.format({"Sales":"${:,.2f}","Units":"{:,.0f}"}),use_container_width=True,hide_index=True)
st.subheader("SKU年度目标排名")
st.dataframe(sku.style.format({"Annual_Sales":"${:,.2f}","Annual_Units":"{:,.0f}"}),use_container_width=True,hide_index=True)

st.header("导出Excel")
hist_month=hist.groupby(["Channel","Month","SKU"],as_index=False).agg(Historical_Units=("Units","sum"),Historical_Amount=("Amount","sum"))
book=export_excel({"Monthly Target":monthly,"Channel Target":channel,"SKU Annual Target":sku,"SKU Monthly Detail":final,"2026 Historical SKU":sku_hist,"2026 Historical Monthly":hist_month,"SKU Assumptions":edited,"Monthly Weights":weight_edit})
st.download_button("下载FY2027预测工作簿",book,file_name=f"Waykar_FY{forecast_year}_Sales_Forecast.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",type="primary")

st.caption("正式用于公司GMV预算前，请确认DF/VDF金额口径一致性、DF数量是否为POS销量、取消/拒绝订单过滤规则，以及重复PO行处理规则。")
