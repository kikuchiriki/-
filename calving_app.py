import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re, json, io, hashlib, calendar as _cal
from pathlib import Path
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

try:
    from supabase import create_client as _sb_create
    _HAS_SB = True
except ImportError:
    _HAS_SB = False

BUCKET        = "farms"
FARMS_INDEX   = "farms_index.json"
GESTATION_DAYS = 285

def _farm_key(name):
    return hashlib.md5(str(name).encode("utf-8")).hexdigest()[:16]

@st.cache_resource
def _get_sb():
    if not _HAS_SB: return None
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return _sb_create(url, key)
    except Exception as e:
        st.error(f"Supabase接続エラー: {e}"); return None

def _sb_dl(path):
    sb = _get_sb()
    if not sb: return None
    try: return sb.storage.from_(BUCKET).download(path)
    except: return None

def _sb_list(prefix=""):
    sb = _get_sb()
    if not sb: return []
    try: return sb.storage.from_(BUCKET).list(prefix) or []
    except: return []

@st.cache_data(ttl=30, show_spinner=False)
def _get_farm_index_cached(_ver):
    data = _sb_dl(FARMS_INDEX)
    if data:
        try:
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, dict): return parsed
        except: pass
    return {}

def _get_farm_index():
    return _get_farm_index_cached(st.session_state.get("ver", 0))

def verify_farm_key(key):
    idx = _get_farm_index()
    name = idx.get(key)
    if name is None: return None, None
    return name, key

COL_MAP = {
    "id":         ["ID","id","牛番号","COWID"],
    "lact":       ["LACT","lact","産次","産次数"],
    "date":       ["DATE","Date","date","日付","EVENT_DATE"],
    "sire":       ["SIRE","sire","Remark","REMARK","STRAW","精液コード","BULL","Sire","B"],
    "preg":       ["R","PREG","preg","受胎結果","Preg"],
}

def find_col(df, key):
    for c in COL_MAP.get(key, [key]):
        if c in df.columns: return c
    return None

def classify_semen(c):
    if not isinstance(c, str): return "その他"
    c = c.strip().upper()
    if not c or c == "-": return "その他"
    if "IVF" in c or c.startswith("ET"): return "F1移植"
    if c.startswith("WA") or c == "WAGYU": return "和牛登録卵"
    if c.startswith("NR"): return "和牛無登録卵"
    if re.match(r'^\d{3,}H\d{3,}X?$', c): return "H判別"
    if re.match(r'^\d+H\d+X$', c): return "H判別"
    if re.match(r'^\d+H\d+$', c): return "H通常"
    if re.match(r'^[A-Z]', c): return "F1授精"
    return "その他"

def map_preg(val):
    if pd.isna(val) or str(val).strip() == "": return None
    v = str(val).strip().upper()
    if v == "P": return 1
    if v == "O": return 0
    return None

def parse_date_col(series):
    for fmt in ["%m/%d/%y", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d"]:
        try: return pd.to_datetime(series, format=fmt)
        except: pass
    return pd.to_datetime(series, errors="coerce")

def read_csv_auto(f):
    for enc in ["utf-8", "cp932", "utf-8-sig"]:
        try:
            if hasattr(f, "seek"): f.seek(0)
            return pd.read_csv(f, encoding=enc)
        except: pass
    if hasattr(f, "seek"): f.seek(0)
    return pd.read_csv(f, encoding="utf-8", errors="replace")

@st.cache_data(ttl=60, show_spinner=False)
def _load_df_cached(farm_key_hash, keywords_t, _ver):
    items = _sb_list(farm_key_hash)
    for item in items:
        if item.get("id") is None: continue
        stem = item["name"].rsplit(".", 1)[0].lower()
        for kw in keywords_t:
            if kw in stem:
                data = _sb_dl(f"{farm_key_hash}/{item['name']}")
                if data is None: return None
                bio = io.BytesIO(data)
                try:
                    if item["name"].lower().endswith((".xlsx", ".xls")):
                        return pd.read_excel(bio)
                    else:
                        return read_csv_auto(bio)
                except: return None
    return None

def load_bred(farm_key_hash):
    return _load_df_cached(farm_key_hash, ("bred","授精","insem"), st.session_state.get("ver",0))

def process_bred(df):
    if df is None: return None
    df = df.copy(); df.columns = [c.strip() for c in df.columns]
    sire_c = find_col(df,"sire"); lact_c = find_col(df,"lact")
    dt_c   = find_col(df,"date"); preg_c  = find_col(df,"preg")
    if sire_c: df["_category"] = df[sire_c].apply(classify_semen)
    if dt_c:
        df["_date"] = parse_date_col(df[dt_c])
    if lact_c:
        df["_lact"] = pd.to_numeric(df[lact_c], errors="coerce").fillna(0).astype(int)
        df = df[df["_lact"]<=20].copy()
    if preg_c:
        df["_preg"] = df[preg_c].apply(map_preg)
    return df

BREED_MAP = {
    "H判別":"ホルスタイン","H通常":"ホルスタイン",
    "F1授精":"F1","F1移植":"F1",
    "和牛登録卵":"和牛","和牛無登録卵":"和牛","その他":"その他"
}
BREED_COLORS = {
    "ホルスタイン":"#2980b9","F1":"#8e44ad","和牛":"#e67e22","その他":"#95a5a6"
}

def build_calving_df(bred_df, n_months=18):
    if bred_df is None or "_preg" not in bred_df.columns or "_date" not in bred_df.columns:
        return pd.DataFrame(), []
    df = bred_df[bred_df["_preg"]==1].copy()
    df = df[df["_date"].notna()]
    if df.empty: return pd.DataFrame(), []
    id_c = find_col(df,"id")
    if id_c and id_c in df.columns:
        df = df.sort_values("_date").groupby(id_c).last().reset_index()
    df["予定分娩日"] = df["_date"] + pd.Timedelta(days=GESTATION_DAYS)
    df["分娩月"]   = df["予定分娩日"].dt.strftime("%Y/%m")
    df["産次区分"] = df["_lact"].apply(
        lambda x: "初産（1産）" if x==0 else "2産" if x==1 else "3産以上" if x>=2 else "不明"
    ) if "_lact" in df.columns else "不明"
    df["品種"] = df["_category"].map(BREED_MAP).fillna("その他") if "_category" in df.columns else "その他"
    fmonths = [(date.today()+relativedelta(months=i)).strftime("%Y/%m") for i in range(n_months)]
    return df[df["分娩月"].isin(fmonths)].copy(), fmonths

# ── ページ設定 ──
st.set_page_config(page_title="分娩予定リスト", layout="wide")
st.markdown("""<style>
.stTabs [data-baseweb="tab-list"]{position:sticky;top:3.2rem;z-index:100;
background-color:white;padding-top:4px;box-shadow:0 2px 4px rgba(0,0,0,.08)}
</style>""", unsafe_allow_html=True)

# ── URL認証 ──
params    = st.query_params
farm_key  = params.get("farm", "")
if not farm_key:
    st.error("URLが無効です。配布されたURLから再度アクセスしてください。")
    st.stop()

farm_name, farm_key_hash = verify_farm_key(farm_key)
if farm_name is None:
    st.error("この農場キーは登録されていません。")
    st.stop()

# ── ヘッダー ──
st.markdown(
    f"<div style='font-size:1.5rem;font-weight:700;padding:8px 0 4px 0;"
    f"position:sticky;top:0;z-index:200;background:white;"
    f"box-shadow:0 1px 4px rgba(0,0,0,.07)'>"
    f"分娩予定リスト　【{farm_name}】</div>",
    unsafe_allow_html=True)

# ── データ読み込み ──
bred_df_raw = load_bred(farm_key_hash)
bred_df     = process_bred(bred_df_raw)

if bred_df is None:
    st.info("授精記録データがまだ登録されていません。管理者にお問い合わせください。")
    st.stop()

n_fc_months = st.slider("表示月数", 6, 18, 12, 1)
df_calv, fmonths_fc = build_calving_df(bred_df, n_fc_months)

if df_calv.empty:
    st.warning("受胎確認済みの授精記録がありません。")
    st.stop()

TABS = st.tabs(["月別サマリー","産次別・品種別","分娩カレンダー","一覧リスト"])

# ── TAB 1: 月別サマリー ──
with TABS[0]:
    st.subheader("月別 分娩予測頭数")
    total_by_m = df_calv.groupby("分娩月").size().reindex(fmonths_fc, fill_value=0)
    fig_tot = go.Figure()
    fig_tot.add_trace(go.Bar(
        x=fmonths_fc, y=total_by_m.tolist(),
        marker_color="#2c3e50",
        text=[str(v) if v>0 else "" for v in total_by_m],
        textposition="outside"))
    fig_tot.update_layout(
        height=360, yaxis_title="頭数",
        margin=dict(t=20,b=10,r=20),
        plot_bgcolor="rgba(250,250,252,1)",
        yaxis=dict(rangemode="tozero"))
    st.plotly_chart(fig_tot, use_container_width=True)

    # 月別サマリーテーブル
    tbl = df_calv.groupby("分娩月").agg(
        総頭数=("分娩月","count"),
        初産=("産次区分", lambda x: (x=="初産（1産）").sum()),
        ホルスタイン=("品種", lambda x: (x=="ホルスタイン").sum()),
        F1=("品種", lambda x: (x=="F1").sum()),
        和牛=("品種", lambda x: (x=="和牛").sum()),
    ).reindex(fmonths_fc, fill_value=0)
    st.dataframe(tbl, use_container_width=True)

# ── TAB 2: 産次別・品種別 ──
with TABS[1]:
    col_l, col_r = st.columns(2)
    lact_order  = ["初産（1産）","2産","3産以上","不明"]
    lact_colors = {"初産（1産）":"#27ae60","2産":"#2980b9","3産以上":"#8e44ad","不明":"#95a5a6"}

    with col_l:
        st.subheader("月別 × 産次別")
        pvt_l = df_calv.groupby(["分娩月","産次区分"]).size().unstack(fill_value=0)
        fig_l = go.Figure()
        for lg in lact_order:
            if lg not in pvt_l.columns: continue
            vs = pvt_l[lg].reindex(fmonths_fc, fill_value=0).tolist()
            fig_l.add_trace(go.Bar(x=fmonths_fc,y=vs,name=lg,marker_color=lact_colors[lg],
                text=[str(v) if v>0 else "" for v in vs],
                textposition="inside",insidetextanchor="middle"))
        fig_l.update_layout(barmode="stack",height=400,yaxis_title="頭数",
            legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=10,b=10))
        st.plotly_chart(fig_l, use_container_width=True)

    with col_r:
        st.subheader("月別 × 品種別")
        breed_order = ["ホルスタイン","F1","和牛","その他"]
        pvt_b = df_calv.groupby(["分娩月","品種"]).size().unstack(fill_value=0)
        fig_b = go.Figure()
        for bg in breed_order:
            if bg not in pvt_b.columns: continue
            vs = pvt_b[bg].reindex(fmonths_fc, fill_value=0).tolist()
            fig_b.add_trace(go.Bar(x=fmonths_fc,y=vs,name=bg,marker_color=BREED_COLORS[bg],
                text=[str(v) if v>0 else "" for v in vs],
                textposition="inside",insidetextanchor="middle"))
        fig_b.update_layout(barmode="stack",height=400,yaxis_title="頭数",
            legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=10,b=10))
        st.plotly_chart(fig_b, use_container_width=True)

# ── TAB 3: 分娩カレンダー ──
with TABS[2]:
    st.subheader("分娩カレンダー")
    cal_month = st.selectbox("表示月を選択", fmonths_fc, key="cal_sel")
    df_cal = df_calv[df_calv["分娩月"]==cal_month].copy()

    if df_cal.empty:
        st.info("この月の分娩予定はありません。")
    else:
        cal_year  = int(cal_month.split("/")[0])
        cal_mon   = int(cal_month.split("/")[1])
        first_wday, n_days = _cal.monthrange(cal_year, cal_mon)
        id_c = find_col(bred_df,"id")
        events_by_day = {}
        for _, row in df_cal.iterrows():
            d = row["予定分娩日"].day if pd.notna(row["予定分娩日"]) else None
            if d is None: continue
            col_hex = BREED_COLORS.get(row["品種"],"#95a5a6")
            cow_id  = str(row[id_c]) if id_c and id_c in row.index else ""
            tag = (f'<span style="font-size:0.72rem;background:{col_hex};color:#fff;'
                   f'border-radius:3px;padding:1px 5px;margin:1px;display:inline-block">'
                   f'{cow_id} ({row["品種"]}/{row["産次区分"]})</span>')
            events_by_day.setdefault(d, []).append(tag)

        week_headers = ["月","火","水","木","金","土","日"]
        html = ('<style>.calw{border-collapse:collapse;width:100%}'
                '.calw th{background:#1F4E79;color:#fff;padding:7px;text-align:center;font-size:.9rem}'
                '.calw td{border:1px solid #ddd;vertical-align:top;padding:5px;min-height:70px;'
                'width:14.28%;font-size:.8rem}'
                '.calw .day-num{font-weight:700;color:#333;margin-bottom:3px}'
                '.calw .weekend{background:#fafafa}.calw .empty{background:#f5f5f5}'
                '.calw .today{background:#fffde7;border:2px solid #f39c12}</style>')
        today_day = date.today().day if (date.today().year==cal_year and date.today().month==cal_mon) else -1
        html += f'<table class="calw"><tr>'
        for wh in week_headers: html += f'<th>{wh}</th>'
        html += '</tr><tr>'
        for _ in range(first_wday): html += '<td class="empty"></td>'
        for day in range(1, n_days+1):
            wday = (first_wday + day - 1) % 7
            cls = "weekend" if wday in (5,6) else ""
            if day == today_day: cls = "today"
            evs = "".join(events_by_day.get(day,[]))
            html += f'<td class="{cls}"><div class="day-num">{day}</div>{evs}</td>'
            if wday == 6 and day < n_days: html += '</tr><tr>'
        remaining = (6 - (first_wday + n_days - 1) % 7) % 7
        for _ in range(remaining): html += '<td class="empty"></td>'
        html += '</tr></table>'
        st.markdown(html, unsafe_allow_html=True)

        st.markdown("---")
        show_cols = ["予定分娩日","産次区分","品種"]
        if id_c and id_c in df_cal.columns: show_cols = [id_c] + show_cols
        disp_cal = df_cal[show_cols].copy()
        if id_c: disp_cal = disp_cal.rename(columns={id_c:"牛番号"})
        disp_cal["予定分娩日"] = disp_cal["予定分娩日"].dt.strftime("%Y/%m/%d")
        st.caption(f"この月の分娩予定: {len(disp_cal)}頭")
        st.dataframe(disp_cal.reset_index(drop=True), use_container_width=True)

# ── TAB 4: 一覧リスト ──
with TABS[3]:
    st.subheader("分娩予定 一覧")
    id_c = find_col(bred_df,"id")
    show_cols = ["予定分娩日","分娩月","産次区分","品種"]
    if id_c and id_c in df_calv.columns: show_cols = [id_c] + show_cols
    disp_all = df_calv[show_cols].copy().sort_values("予定分娩日")
    if id_c: disp_all = disp_all.rename(columns={id_c:"牛番号"})
    disp_all["予定分娩日"] = disp_all["予定分娩日"].dt.strftime("%Y/%m/%d")
    disp_all = disp_all.reset_index(drop=True)
    st.caption(f"合計 {len(disp_all)}頭")

    # フィルター
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        breed_opts = ["全品種"] + [b for b in ["ホルスタイン","F1","和牛","その他"] if b in df_calv["品種"].unique()]
        sel_breed = st.selectbox("品種フィルター", breed_opts)
    with fc2:
        lact_opts = ["全産次"] + [l for l in ["初産（1産）","2産","3産以上"] if l in df_calv["産次区分"].unique()]
        sel_lact = st.selectbox("産次フィルター", lact_opts)
    with fc3:
        month_opts = ["全期間"] + fmonths_fc
        sel_mon = st.selectbox("月フィルター", month_opts)

    filtered = df_calv.copy()
    if sel_breed != "全品種": filtered = filtered[filtered["品種"]==sel_breed]
    if sel_lact  != "全産次":  filtered = filtered[filtered["産次区分"]==sel_lact]
    if sel_mon   != "全期間":  filtered = filtered[filtered["分娩月"]==sel_mon]

    show_filtered = ["予定分娩日","分娩月","産次区分","品種"]
    if id_c and id_c in filtered.columns: show_filtered = [id_c] + show_filtered
    disp_f = filtered[show_filtered].copy().sort_values("予定分娩日")
    if id_c: disp_f = disp_f.rename(columns={id_c:"牛番号"})
    disp_f["予定分娩日"] = disp_f["予定分娩日"].dt.strftime("%Y/%m/%d")
    st.caption(f"表示件数: {len(disp_f)}頭")
    st.dataframe(disp_f.reset_index(drop=True), use_container_width=True)
