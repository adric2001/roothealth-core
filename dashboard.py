import streamlit as st
import boto3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import time
import json
from pycognito import Cognito
from boto3.dynamodb.conditions import Key
from datetime import datetime
from dateutil.relativedelta import relativedelta

REGION = os.environ.get('AWS_REGION', 'us-east-1')
USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'RootHealth_Stats')
SUPPLEMENTS_TABLE = "RootHealth_Supplements"
RELATIONSHIPS_TABLE = "RootHealth_Relationships"
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'roothealth-raw-files-adric') 

st.set_page_config(page_title="RootHealth OS", page_icon="🧬", layout="wide", initial_sidebar_state="collapsed")

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

    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        background-color: transparent;
        border-radius: 4px;
        color: #8F9BB3;
        font-weight: 600;
        padding: 8px 16px;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        color: #FFFFFF;
        background-color: #2C2F3A;
    }

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
        
    if delta_val == 0 and time_str == "New":
        delta_html = '<span class="delta-neutral">✨ First Record</span>'

    html = f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">
            {value} <span class="metric-unit">{unit}</span>
        </div>
        <div>{delta_html}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

def get_time_diff(d1, d2):
    diff = relativedelta(d1, d2)
    if diff.years > 0: return f"{diff.years}y"
    if diff.months > 0: return f"{diff.months}mo"
    if diff.weeks > 0: return f"{diff.weeks}w"
    return f"{diff.days}d"

def save_user_preferences(metrics_list):
    try:
        table.put_item(Item={
            'user_id': st.session_state.username,
            'record_id': 'USER_SETTINGS', 
            'favorites': metrics_list,
            'upload_timestamp': str(int(time.time()))
        })
        return True
    except: return False

def get_user_preferences():
    try:
        response = table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_SETTINGS'})
        if 'Item' in response: return response['Item'].get('favorites', [])
    except: pass
    return ["Testosterone, Total", "Vitamin D", "Ferritin", "Body Weight"]

def save_user_profile(age, height, gender, goal, weight):
    try:
        table.put_item(Item={
            'user_id': st.session_state.username,
            'record_id': 'USER_PROFILE', 
            'age': age, 'height': height, 'gender': gender, 'goal': goal, 'weight': weight,
            'upload_timestamp': str(int(time.time()))
        })
        st.success("Saved!")
    except: st.error("Error saving profile")

def get_user_profile():
    try: return table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_PROFILE'}).get('Item', {})
    except: return {}

def init_auth(username=None):
    if not USER_POOL_ID or not CLIENT_ID: st.stop()
    return Cognito(USER_POOL_ID, CLIENT_ID, username=username)

def login_user(username, password):
    try: u = init_auth(username); u.authenticate(password=password); return u
    except: return None

def register_user(email, password):
    try: u = init_auth(email); u.set_base_attributes(email=email); u.register(email, password); st.success("Check email for code.")
    except: st.error("Registration failed")

def confirm_user(email, code):
    try: u = init_auth(email); u.confirm_sign_up(code, username=email); st.success("Verified!")
    except: st.error("Verification failed")

def run_ai_coach(user_data, user_stack, user_profile):
    csv_data = user_data.to_csv(index=False)
    stack_txt = "\n".join([f"- {s['item_name']} ({s['dosage']} {s['frequency']})" for s in user_stack]) if user_stack else "None"
    
    prof_txt = f"Age: {user_profile.get('age','?')}\nGoal: {user_profile.get('goal','Health')}\nWeight: {user_profile.get('weight','?')}"
    
    prompt = f"Role: Elite Health Coach. Context: {prof_txt}. Labs: {csv_data}. Stack: {stack_txt}. Task: 1. Analysis 2. Stack Audit 3. Protocol. Tone: Direct."
    
    body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 2500, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]})
    try:
        r = bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20240620-v1:0", body=body)
        return json.loads(r['body'].read())['content'][0]['text']
    except Exception as e: return f"AI Error: {e}"

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
    st.header("Quick Actions")
    with st.expander("📝 Log Weight", expanded=True):
        with st.form("log"):
            w = st.number_input("Weight (lbs)", step=0.1)
            if st.form_submit_button("Save"):
                ts = str(int(time.time()))
                table.put_item(Item={'user_id': st.session_state.username, 'record_id': f"Weight_{ts}", 'metric': "Body Weight", 'value': str(w), 'unit': 'lbs', 'upload_timestamp': ts})
                st.success("Saved"); time.sleep(0.5); st.rerun()
    if st.button("Log Out"): st.session_state.authenticated = False; st.rerun()

tabs = st.tabs(["Overview", "Profile", "AI Coach", "Upload", "Stack", "Coaching"])

def get_data(uid):
    try: return [i for i in table.query(KeyConditionExpression=Key('user_id').eq(uid))['Items']]
    except: return []

raw_data = get_data(st.session_state.username)
df = pd.DataFrame(raw_data)

if not df.empty:
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    df['Date'] = pd.to_datetime(pd.to_numeric(df['upload_timestamp'].fillna(0)), unit='s')
    df = df.dropna(subset=['value']).sort_values(by='Date')

with tabs[0]:
    if df.empty: 
        st.info("👋 Welcome! Upload your first lab PDF in the 'Upload' tab.")
    else:
        all_metrics = sorted(df['metric'].unique().tolist())
        saved_faves = get_user_preferences()
        current_faves = [m for m in saved_faves if m in all_metrics]
        
        c1, c2 = st.columns([3, 1])
        c1.subheader("Dashboard")
        with c2.popover("⚙️ Customize"):
            new_faves = st.multiselect("Visible Metrics", all_metrics, default=current_faves)
            if st.button("Save View"):
                save_user_preferences(new_faves); st.rerun()

        if not current_faves:
            st.warning("No metrics selected. Use the ⚙️ button to add some.")
        else:
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

                with cols[i % 3]:
                    render_metric_card(metric, val, unit, delta, pct, t_str)

        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("Trends")
        sel = st.selectbox("", all_metrics, label_visibility="collapsed")
        
        chart_data = df[df['metric'] == sel]
        fig = px.line(chart_data, x="Date", y="value", markers=True, title=None)
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#8F9BB3", 
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#2C2F3A"),
            margin=dict(l=0, r=0, t=10, b=0),
            height=300
        )
        fig.update_traces(line_color="#4CAF50", line_width=3, marker_size=8)
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("🔍 Debug Raw Data"):
        st.dataframe(df)

with tabs[1]:
    st.subheader("Bio Profile")
    prof = get_user_profile()
    with st.form("prof"):
        c1, c2 = st.columns(2)
        age = c1.number_input("Age", value=int(prof.get('age', 25)))
        h = c2.text_input("Height", value=prof.get('height', ""))
        g = c1.selectbox("Gender", ["Male", "Female"], index=0)
        goal = c2.selectbox("Goal", ["Longevity", "Hypertrophy", "Fat Loss", "Energy"])
        if st.form_submit_button("Save Changes"):
            save_user_profile(age, h, g, goal, prof.get('weight', 0))
            st.rerun()

with tabs[2]:
    st.subheader("AI Analysis")
    if st.button("⚡ Generate Report", type="primary"):
        with st.spinner("Analyzing..."):
            try: stack = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
            except: stack = []
            res = run_ai_coach(df, stack, get_user_profile())
            st.markdown(res)

with tabs[3]:
    st.subheader("Upload Labs")
    files = st.file_uploader("PDF/CSV", accept_multiple_files=True)
    if files and st.button("Upload"):
        for f in files: s3.put_object(Bucket=BUCKET_NAME, Key=f"uploads/{st.session_state.username}/{f.name}", Body=f.getvalue())
        st.success("Uploaded! Processing in background...")

with tabs[4]:
    st.subheader("Protocol")
    c1, c2 = st.columns([2,1])
    with c2:
        with st.form("add_supp"):
            n = st.text_input("Name"); d = st.text_input("Dose"); f = st.selectbox("Freq", ["Daily", "AM/PM", "Weekly"])
            if st.form_submit_button("Add"): supp_table.put_item(Item={'user_id': st.session_state.username, 'item_name': n, 'dosage': d, 'frequency': f}); st.rerun()
    with c1:
        try: 
            items = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
            if items: st.dataframe(pd.DataFrame(items)[['item_name', 'dosage', 'frequency']], use_container_width=True, hide_index=True)
        except: pass

with tabs[5]:
    st.subheader("Coach Portal")
    role = st.radio("I am a:", ["Client", "Coach"], horizontal=True)
    if role == "Client":
        coach_em = st.text_input("Coach Email")
        if st.button("Grant Access"): rel_table.put_item(Item={'coach_id': coach_em.lower(), 'client_id': st.session_state.username}); st.success("Linked!")
    else:
        st.info("Coach Dashboard")
        try:
            clients = rel_table.query(KeyConditionExpression=Key('coach_id').eq(st.session_state.username))['Items']
            if not clients: st.warning("No clients linked.")
            else:
                c_sel = st.selectbox("Select Client", [c['client_id'] for c in clients])
                if c_sel:
                    c_data = get_data(c_sel)
                    st.dataframe(pd.DataFrame(c_data))
        except: st.error("Error loading clients")