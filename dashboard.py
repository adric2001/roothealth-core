import streamlit as st
import boto3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import time
import json
import re
from pycognito import Cognito
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

REGION = os.environ.get('AWS_REGION', 'us-east-1')
USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'RootHealth_Stats')
SUPPLEMENTS_TABLE = "RootHealth_Supplements"
RELATIONSHIPS_TABLE = "RootHealth_Relationships"
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'roothealth-raw-files-adric') 

st.set_page_config(page_title="RootHealth OS", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 5rem; max_width: 1200px; }
    
    .metric-card {
        background-color: #1A1C24;
        border: 1px solid #2C2F3A;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: transform 0.2s;
    }
    .metric-card:hover { transform: translateY(-2px); border-color: #4CAF50; }
    
    .metric-label { color: #8F9BB3; font-size: 0.85rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-value { color: #FFFFFF; font-size: 1.6rem; font-weight: 700; margin: 4px 0; }
    .metric-unit { color: #5F6B7C; font-size: 0.9rem; font-weight: 400; margin-left: 4px; }
    
    .delta-positive { color: #00E676; font-size: 0.8rem; font-weight: 600; background: rgba(0, 230, 118, 0.1); padding: 2px 6px; border-radius: 4px; }
    .delta-negative { color: #FF5252; font-size: 0.8rem; font-weight: 600; background: rgba(255, 82, 82, 0.1); padding: 2px 6px; border-radius: 4px; }
    .delta-neutral { color: #8F9BB3; font-size: 0.8rem; }

    @media (max-width: 600px) {
        .metric-value { font-size: 1.4rem; }
    }
</style>
""", unsafe_allow_html=True)

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
supp_table = dynamodb.Table(SUPPLEMENTS_TABLE)
rel_table = dynamodb.Table(RELATIONSHIPS_TABLE)
s3 = boto3.client('s3', region_name=REGION)
bedrock = boto3.client('bedrock-runtime', region_name=REGION)

def render_metric_card(label, value, unit, delta_val, delta_pct, time_str):
    if delta_val > 0:
        delta_html = f'<span class="delta-positive">▲ {delta_val:.1f} ({delta_pct:.0f}%)</span> <span class="delta-neutral">in {time_str}</span>'
    elif delta_val < 0:
        delta_html = f'<span class="delta-negative">▼ {abs(delta_val):.1f} ({abs(delta_pct):.0f}%)</span> <span class="delta-neutral">in {time_str}</span>'
    else:
        delta_html = '<span class="delta-neutral">No change</span>'
    if delta_val == 0 and time_str == "New": delta_html = '<span class="delta-neutral">✨ First Record</span>'
    html = f"""<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value} <span class="metric-unit">{unit}</span></div><div>{delta_html}</div></div>"""
    st.markdown(html, unsafe_allow_html=True)

def get_time_diff(d1, d2):
    diff = relativedelta(d1, d2)
    if diff.years > 0: return f"{diff.years}y"
    if diff.months > 0: return f"{diff.months}mo"
    if diff.weeks > 0: return f"{diff.weeks}w"
    return f"{diff.days}d"

def parse_height_to_inches(h_str):
    if not h_str: return 70
    try:
        if "'" in h_str:
            parts = h_str.split("'")
            ft = int(parts[0])
            inch = int(parts[1]) if len(parts) > 1 and parts[1] else 0
            return (ft * 12) + inch
        return int(h_str)
    except: return 70

def get_optimal_ranges(profile):
    gender = profile.get('gender', 'Male')
    height_str = profile.get('height', '5\'10')
    inches = parse_height_to_inches(height_str)
    
    min_w = int(18.5 * (inches**2) / 703)
    max_w = int(25 * (inches**2) / 703)
    opt_min_w = int(21 * (inches**2) / 703)
    opt_max_w = int(24 * (inches**2) / 703)

    ranges = {
        "Vitamin D": {"min": 30, "max": 100, "opt_min": 50, "opt_max": 80, "unit": "ng/mL"},
        "TSH": {"min": 0.4, "max": 4.5, "opt_min": 0.5, "opt_max": 2.0, "unit": "mIU/L"},
        "Sleep Duration": {"min": 0, "max": 12, "opt_min": 7, "opt_max": 9, "unit": "hrs"},
        "Body Weight": {"min": min_w, "max": max_w, "opt_min": opt_min_w, "opt_max": opt_max_w, "unit": "lbs"}
    }
    
    if gender == "Male":
        ranges.update({
            "Testosterone, Total": {"min": 264, "max": 916, "opt_min": 700, "opt_max": 1100, "unit": "ng/dL"},
            "Ferritin": {"min": 24, "max": 336, "opt_min": 100, "opt_max": 250, "unit": "ng/mL"},
            "Estradiol": {"min": 7, "max": 50, "opt_min": 20, "opt_max": 35, "unit": "pg/mL"}
        })
    else:
        ranges.update({
            "Testosterone, Total": {"min": 15, "max": 70, "opt_min": 35, "opt_max": 65, "unit": "ng/dL"},
            "Ferritin": {"min": 11, "max": 307, "opt_min": 50, "opt_max": 150, "unit": "ng/mL"},
            "Estradiol": {"min": 15, "max": 350, "opt_min": 50, "opt_max": 200, "unit": "pg/mL"}
        })
    return ranges

def save_user_preferences(metrics_list):
    try: table.put_item(Item={'user_id': st.session_state.username, 'record_id': 'USER_SETTINGS', 'favorites': metrics_list, 'upload_timestamp': str(int(time.time()))}); return True
    except: return False

def get_user_preferences():
    try: return table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_SETTINGS'}).get('Item', {}).get('favorites', [])
    except: return ["Testosterone, Total", "Vitamin D", "Ferritin", "Body Weight"]

def save_user_profile(age, height, gender, goal, weight):
    try: 
        table.put_item(Item={'user_id': st.session_state.username, 'record_id': 'USER_PROFILE', 'age': age, 'height': height, 'gender': gender, 'goal': goal, 'weight': weight, 'upload_timestamp': str(int(time.time()))})
        st.success("Profile Saved!")
        time.sleep(1) # Wait for propagation
    except: st.error("Error saving profile")

def get_user_profile():
    try: return table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_PROFILE'}).get('Item', {})
    except: return {}

def update_manual_data(df_changes):
    for index, row in df_changes.iterrows():
        try:
            ts = str(int(row['Date'].timestamp())) if pd.notnull(row['Date']) else str(int(time.time()))
            rec_id = row.get('record_id')
            if not rec_id or pd.isna(rec_id): rec_id = f"{str(row['metric']).replace(' ', '_')}_{ts}"
            table.put_item(Item={'user_id': st.session_state.username, 'record_id': rec_id, 'metric': row['metric'], 'value': str(row['value']), 'unit': row['unit'], 'upload_timestamp': ts, 'source_file': 'Manual_Edit'})
        except Exception as e: st.error(f"Failed: {e}")

def init_auth(username=None):
    if not USER_POOL_ID or not CLIENT_ID: st.stop()
    return Cognito(USER_POOL_ID, CLIENT_ID, username=username)

def login_user(username, password):
    try: u = init_auth(username); u.authenticate(password=password); return u
    except: return None

def register_user(email, password):
    try: u = init_auth(email); u.set_base_attributes(email=email); u.register(email, password); st.success("Check email for code."); return True
    except: st.error("Failed"); return False

def confirm_user(email, code):
    try: u = init_auth(email); u.confirm_sign_up(code, username=email); st.success("Verified!"); return True
    except: st.error("Failed"); return False

def run_ai_coach(user_data, user_stack, user_profile):
    csv_data = user_data.to_csv(index=False)
    stack_txt = "\n".join([f"- {s['item_name']} ({s['dosage']} {s['frequency']})" for s in user_stack]) if user_stack else "None"
    prof_txt = f"Age: {user_profile.get('age','?')}\nGoal: {user_profile.get('goal','Health')}\nWeight: {user_profile.get('weight','?')}\nHeight: {user_profile.get('height','?')}\nGender: {user_profile.get('gender','?')}"
    prompt = f"Role: Elite Biohacker Coach. Context: {prof_txt}. Labs: {csv_data}. Stack: {stack_txt}. Task: 1. Analysis 2. Stack Audit 3. Protocol. Tone: Direct."
    body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 2500, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]})
    try: return json.loads(bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20240620-v1:0", body=body)['body'].read())['content'][0]['text']
    except Exception as e: return f"AI Error: {e}"

def get_data(uid):
    try: return [i for i in table.query(KeyConditionExpression=Key('user_id').eq(uid))['Items']]
    except: return []

if 'authenticated' not in st.session_state: st.session_state.authenticated = False
if 'username' not in st.session_state: st.session_state.username = None

if not st.session_state.authenticated:
    st.title("🧬 RootHealth")
    t1, t2, t3 = st.tabs(["Log In", "Sign Up", "Verify"])
    with t1:
        e, p = st.text_input("Email"), st.text_input("Password", type="password")
        if st.button("Log In"): 
            if login_user(e, p): st.session_state.authenticated = True; st.session_state.username = e; st.rerun()
    with t2:
        ne, np = st.text_input("New Email"), st.text_input("New Password", type="password")
        ic = st.text_input("Invite Code", type="password")
        if st.button("Join"): 
            if ic == os.environ.get("INVITE_CODE"): register_user(ne, np)
            else: st.error("Invalid Code")
    with t3:
        ve, vc = st.text_input("Verify Email"), st.text_input("Code")
        if st.button("Verify"): confirm_user(ve, vc)
    st.stop()

with st.sidebar:
    st.title("🧬 RootHealth")
    page = st.radio("Navigation", ["Dashboard", "Data Manager", "AI Coach", "Profile & Stack", "Coaching"], label_visibility="collapsed")
    st.markdown("---")
    st.caption("Daily Bio-Log")
    with st.form("quick_log"):
        c1, c2 = st.columns(2)
        w = c1.number_input("Weight", step=0.1)
        sleep = c2.number_input("Sleep (hrs)", step=0.5, min_value=0.0, max_value=24.0)
        c3, c4 = st.columns(2)
        energy = c3.slider("Energy", 1, 10, 5)
        stress = c4.slider("Stress", 1, 10, 5)
        if st.form_submit_button("Save Log"):
            ts = str(int(time.time()))
            for n,v,u in [("Body Weight",w,"lbs"),("Sleep Duration",sleep,"hrs"),("Energy Level",energy,"/10"),("Stress Level",stress,"/10")]:
                table.put_item(Item={'user_id': st.session_state.username, 'record_id': f"{n.replace(' ','_')}_{ts}", 'metric': n, 'value': str(v), 'unit': u, 'upload_timestamp': ts, 'source_file': 'Daily_Log'})
            st.success("Logged!"); time.sleep(1); st.rerun()
    if st.button("Log Out"): st.session_state.authenticated = False; st.rerun()

raw_data = get_data(st.session_state.username)
df = pd.DataFrame(raw_data)
if not df.empty:
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    df['Date'] = pd.to_datetime(pd.to_numeric(df['upload_timestamp'].fillna(0)), unit='s')
    df = df.dropna(subset=['value']).sort_values(by='Date')

prof = get_user_profile()

if page == "Dashboard":
    st.header("Dashboard")
    if df.empty: st.info("👋 Welcome! Upload labs or log weight to start.")
    else:
        all_metrics = sorted(df['metric'].unique().tolist())
        saved_faves = get_user_preferences()
        current_faves = [m for m in saved_faves if m in all_metrics]
        
        with st.popover("⚙️ Customize Widgets"):
            new_faves = st.multiselect("Visible Metrics", all_metrics, default=current_faves)
            if st.button("Save Layout"): save_user_preferences(new_faves); st.rerun()

        if current_faves:
            cols = st.columns(3)
            for i, metric in enumerate(current_faves):
                m_df = df[df['metric'] == metric].sort_values(by='Date')
                if m_df.empty: continue
                curr = m_df.iloc[-1]
                val, unit = curr['value'], curr['unit']
                delta, pct, t_str = 0, 0, "New"
                if len(m_df) > 1:
                    prev = m_df.iloc[-2]
                    delta = val - prev['value']
                    pct = (delta / prev['value']) * 100 if prev['value'] != 0 else 0
                    t_str = get_time_diff(curr['Date'], prev['Date'])
                with cols[i % 3]: render_metric_card(metric, val, unit, delta, pct, t_str)
        
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("Consistency")
        
        daily_logs = df[df['source_file'] == 'Daily_Log']
        if not daily_logs.empty:
            daily_counts = daily_logs.groupby(daily_logs['Date'].dt.date).size().reset_index(name='logs')
            daily_counts['Date'] = pd.to_datetime(daily_counts['Date'])
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=90)
            date_range = pd.date_range(start=start_date, end=end_date)
            heatmap_df = pd.DataFrame({'Date': date_range})
            heatmap_df = heatmap_df.merge(daily_counts, on='Date', how='left').fillna(0)
            heatmap_df['Week'] = heatmap_df['Date'].dt.isocalendar().week
            heatmap_df['Day'] = heatmap_df['Date'].dt.dayofweek
            heatmap_df['DayName'] = heatmap_df['Date'].dt.day_name()
            
            heatmap_df['Color'] = heatmap_df['logs'].apply(lambda x: 0 if x==0 else 1 if x==1 else 2 if x<3 else 3)
            
            fig_heat = go.Figure(data=go.Heatmap(
                z=heatmap_df['logs'],
                x=heatmap_df['Week'],
                y=heatmap_df['DayName'],
                colorscale=[[0, '#161B22'], [0.1, '#0E4429'], [0.5, '#006D32'], [1, '#39D353']],
                showscale=False,
                xgap=3, ygap=3
            ))
            fig_heat.update_layout(
                height=180, 
                plot_bgcolor="rgba(0,0,0,0)", 
                paper_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(showgrid=False, zeroline=False, categoryorder='array', categoryarray=['Sunday', 'Saturday', 'Friday', 'Thursday', 'Wednesday', 'Tuesday', 'Monday']),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                margin=dict(l=0,r=0,t=10,b=10)
            )
            st.plotly_chart(fig_heat, use_container_width=True)
        else: st.info("Log daily metrics to see your streak.")

        st.subheader("Deep Dive")
        c1, c2 = st.columns([1, 2])
        with c1:
            sel = st.selectbox("Select Metric", all_metrics)
            latest_val = df[df['metric'] == sel].iloc[-1]['value']
            fig_gauge = go.Figure(go.Indicator(
                mode = "gauge+number", value = latest_val,
                title = {'text': "Current Status"},
                gauge = {
                    'axis': {'range': [0, latest_val * 1.5]},
                    'bar': {'color': "white"},
                    'steps': [{'range': [0, latest_val * 1.5], 'color': "#1A1C24"}],
                }
            ))
            
            dynamic_ranges = get_optimal_ranges(prof)
            
            if sel in dynamic_ranges:
                r = dynamic_ranges[sel]
                fig_gauge.update_traces(gauge={
                    'axis': {'range': [r['min'] * 0.8, r['max'] * 1.1]},
                    'steps': [
                        {'range': [r['min']*0.8, r['opt_min']], 'color': "#FF5252"},
                        {'range': [r['opt_min'], r['opt_max']], 'color': "#00E676"},
                        {'range': [r['opt_max'], r['max']*1.1], 'color': "#FF5252"}
                    ]
                })
            fig_gauge.update_layout(height=250, margin=dict(l=20,r=20,t=30,b=20), paper_bgcolor="rgba(0,0,0,0)", font={'color': "white"})
            st.plotly_chart(fig_gauge, use_container_width=True)
            
        with c2:
            chart_data = df[df['metric'] == sel]
            fig = px.line(chart_data, x="Date", y="value", markers=True)
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#8F9BB3", xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#2C2F3A"), height=300)
            fig.update_traces(line_color="#4CAF50", line_width=3, marker_size=8)
            if sel in dynamic_ranges:
                r = dynamic_ranges[sel]
                fig.add_hrect(y0=r['opt_min'], y1=r['opt_max'], fillcolor="#00E676", opacity=0.1, layer="below", line_width=0)
            st.plotly_chart(fig, use_container_width=True)

elif page == "Data Manager":
    st.header("Data Manager")
    t1, t2 = st.tabs(["📄 Upload Files", "✍️ Manual Editor"])
    with t1:
        files = st.file_uploader("Upload Labs", accept_multiple_files=True)
        if files and st.button("Process Files", type="primary"):
            bar = st.progress(0, text="Uploading...")
            for i, f in enumerate(files):
                s3.put_object(Bucket=BUCKET_NAME, Key=f"uploads/{st.session_state.username}/{f.name}", Body=f.getvalue())
                bar.progress((i + 1) / len(files), text=f"Processed {f.name}")
            st.success("Upload Complete! AI is extracting data now...")
    with t2:
        c1, c2 = st.columns([3, 1])
        c1.write("Edit Data")
        if not df.empty: c2.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "data.csv", "text/csv")
        edit_df = df[['metric', 'value', 'unit', 'Date', 'record_id']].copy() if not df.empty else pd.DataFrame(columns=['metric', 'value', 'unit', 'Date', 'record_id'])
        edited_df = st.data_editor(edit_df, num_rows="dynamic", use_container_width=True, hide_index=True)
        if st.button("Save Changes"): update_manual_data(edited_df); st.success("Updated!"); time.sleep(1); st.rerun()

elif page == "AI Coach":
    st.header("Intelligence Center")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Correlations")
        if df.empty: st.warning("No data.")
        else:
            all_m = df['metric'].unique()
            m1 = st.selectbox("Left Axis", all_m, index=0)
            m2 = st.selectbox("Right Axis", all_m, index=1 if len(all_m)>1 else 0)
            d1 = df[df['metric'] == m1].sort_values('Date')
            d2 = df[df['metric'] == m2].sort_values('Date')
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=d1['Date'], y=d1['value'], name=m1, mode='lines+markers'))
            fig.add_trace(go.Scatter(x=d2['Date'], y=d2['value'], name=m2, mode='lines+markers', yaxis='y2'))
            fig.update_layout(yaxis=dict(title=m1), yaxis2=dict(title=m2, overlaying='y', side='right'), legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.subheader("AI Protocol Coach")
        if st.button("⚡ Run Full Audit", type="primary"):
            with st.spinner("Analyzing..."):
                try: stack = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                except: stack = []
                res = run_ai_coach(df, stack, prof)
                st.markdown(res)

elif page == "Profile & Stack":
    st.header("Profile & Stack")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Age", prof.get('age', 'N/A'))
        c2.metric("Weight", f"{prof.get('weight', 'N/A')} lbs")
        c3.metric("Height", prof.get('height', 'N/A'))
        with st.expander("Edit Profile"):
            with st.form("prof_form"):
                ec1, ec2 = st.columns(2)
                age = ec1.number_input("Age", value=int(prof.get('age', 25)), step=1)
                weight = ec1.number_input("Weight", value=float(prof.get('weight', 180)))
                gender = ec1.selectbox("Gender", ["Male", "Female"], index=0 if prof.get('gender') == "Male" else 1)
                height = ec2.text_input("Height", value=prof.get('height', ""))
                goal = ec2.selectbox("Goal", ["Optimization / Longevity", "Muscle Gain / Hypertrophy", "Fat Loss", "Cognitive Performance", "Libido / Hormone Health"], index=0)
                if st.form_submit_button("Save"): save_user_profile(age, height, gender, goal, weight); st.rerun()
    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("Stack")
            if st.button("🗑️ Clear Entire Stack", type="secondary"):
                try: 
                    items = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                    for i in items: supp_table.delete_item(Key={'user_id': st.session_state.username, 'item_name': i['item_name']})
                    st.success("Stack Cleared"); time.sleep(1); st.rerun()
                except: st.error("Error clearing")
            try: 
                items = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                for item in items:
                    col_a, col_b, col_c = st.columns([3, 2, 1])
                    col_a.markdown(f"**{item['item_name']}**")
                    col_a.caption(f"{item['dosage']}")
                    col_b.markdown(f"*{item['frequency']}*")
                    if col_c.button("🗑️", key=f"del_{item['item_name']}"): 
                        supp_table.delete_item(Key={'user_id': st.session_state.username, 'item_name': item['item_name']})
                        st.success("Deleted"); time.sleep(1); st.rerun()
                    st.markdown("---")
            except: pass
        with c2:
            st.subheader("Add Item")
            with st.form("stack_add"):
                n = st.text_input("Name"); d = st.text_input("Dose"); f = st.selectbox("Freq", ["Daily", "AM/PM", "Weekly", "EOD"])
                if st.form_submit_button("Add"): supp_table.put_item(Item={'user_id': st.session_state.username, 'item_name': n, 'dosage': d, 'frequency': f}); st.rerun()

elif page == "Coaching":
    st.header("Coaching Portal")
    role = st.radio("I am a:", ["Client", "Coach"], horizontal=True)
    if role == "Client":
        coach_em = st.text_input("Coach Email")
        if st.button("Link Coach"): rel_table.put_item(Item={'coach_id': coach_em.lower(), 'client_id': st.session_state.username}); st.success("Access Granted!")
    else:
        st.subheader("Clients")
        try:
            clients = rel_table.query(KeyConditionExpression=Key('coach_id').eq(st.session_state.username))['Items']
            c_sel = st.selectbox("Select Client", [c['client_id'] for c in clients]) if clients else None
            if c_sel: st.dataframe(pd.DataFrame(get_data(c_sel)))
        except: st.error("Error")