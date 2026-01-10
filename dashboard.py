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
    .block-container { padding-top: 1rem; padding-bottom: 5rem; }
    div.stButton > button { width: 100%; border-radius: 8px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
supp_table = dynamodb.Table(SUPPLEMENTS_TABLE)
rel_table = dynamodb.Table(RELATIONSHIPS_TABLE)
s3 = boto3.client('s3', region_name=REGION)
bedrock = boto3.client('bedrock-runtime', region_name=REGION)

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
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False

def get_user_preferences():
    try:
        response = table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_SETTINGS'})
        if 'Item' in response:
            return response['Item'].get('favorites', [])
    except: pass
    return ["Testosterone, Total", "Vitamin D", "Ferritin", "Body Weight"]

def save_user_profile(age, height, gender, goal, weight):
    try:
        table.put_item(Item={
            'user_id': st.session_state.username,
            'record_id': 'USER_PROFILE', 
            'age': age,
            'height': height,
            'gender': gender,
            'goal': goal,
            'weight': weight,
            'upload_timestamp': str(int(time.time()))
        })
        st.success("Profile Saved!")
    except Exception as e:
        st.error(f"Error saving profile: {e}")

def get_user_profile():
    try:
        response = table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_PROFILE'})
        return response.get('Item', {})
    except: return {}

def init_auth(username=None):
    if not USER_POOL_ID or not CLIENT_ID: st.stop()
    return Cognito(USER_POOL_ID, CLIENT_ID, username=username)

def login_user(username, password):
    try: u = init_auth(username); u.authenticate(password=password); return u
    except: return None

def register_user(email, password):
    try: u = init_auth(email); u.set_base_attributes(email=email); u.register(email, password); st.success("Check email for code.")
    except Exception as e: st.error(f"Error: {e}")

def confirm_user(email, code):
    try: u = init_auth(email); u.confirm_sign_up(code, username=email); st.success("Verified!")
    except Exception as e: st.error(f"Error: {e}")

def run_ai_coach(user_data, user_stack, user_profile):
    csv_data = user_data.to_csv(index=False)
    stack_text = "\n".join([f"- {s['item_name']} ({s['dosage']} {s['frequency']})" for s in user_stack]) if user_stack else "No supplements."
    
    profile_text = f"""
    Age: {user_profile.get('age', 'Unknown')}
    Gender: {user_profile.get('gender', 'Unknown')}
    Height: {user_profile.get('height', 'Unknown')}
    Current Weight: {user_profile.get('weight', 'Unknown')}
    Primary Goal: {user_profile.get('goal', 'Optimization')}
    """

    prompt = f"""
    You are a high-performance Biohacking Coach.
    
    USER PROFILE:
    {profile_text}

    BLOODWORK HISTORY:
    {csv_data}
    
    CURRENT STACK:
    {stack_text}
    
    TASK:
    1. ANALYZE: Correlate their bloodwork with their specific goal ({user_profile.get('goal')}).
    2. AUDIT: Is their stack helping or hurting?
    3. PLAN: Give 3 specific actionable steps (Protocol, Diet, or Training).
    
    Tone: Direct, data-driven, no medical disclaimers.
    """
    
    body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 3000, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]})
    try:
        r = bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20240620-v1:0", body=body)
        return json.loads(r['body'].read())['content'][0]['text']
    except Exception as e: return f"AI Error: {e}"

if 'authenticated' not in st.session_state: st.session_state.authenticated = False
if 'username' not in st.session_state: st.session_state.username = None

if not st.session_state.authenticated:
    st.title("🧬 RootHealth OS")
    t1, t2, t3 = st.tabs(["Log In", "Sign Up", "Verify"])
    with t1:
        e, p = st.text_input("Email"), st.text_input("Password", type="password")
        if st.button("Log In"):
            if login_user(e, p): st.session_state.authenticated = True; st.session_state.username = e; st.rerun()
    with t2:
        ne, np = st.text_input("New Email"), st.text_input("New Password", type="password")
        ic = st.text_input("Invite Code", type="password")
        if st.button("Create"):
            if ic == os.environ.get("INVITE_CODE"): register_user(ne, np)
            else: st.error("Invalid Code")
    with t3:
        ve, vc = st.text_input("Verify Email"), st.text_input("Code")
        if st.button("Verify"): confirm_user(ve, vc)
    st.stop()

with st.sidebar:
    st.title("🧬 RootHealth")
    st.caption("v2.1.0 Beta")
    st.markdown("---")
    if st.button("Log Out"): st.session_state.authenticated = False; st.rerun()

tabs = st.tabs(["📊 Dashboard", "👤 Profile", "🧠 AI Analysis", "📤 Upload", "💊 Stack", "🤝 Coaching"])

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
    st.subheader("Overview")
    if df.empty: 
        st.info("No data yet. Go to Upload tab.")
    else:
        available_metrics = sorted(df['metric'].unique().tolist())
        saved_favorites = get_user_preferences()
        valid_defaults = [m for m in saved_favorites if m in available_metrics]
        
        with st.expander("⚙️ Configure Dashboard Widgets", expanded=False):
            selected_kpis = st.multiselect("Select Key Biomarkers:", options=available_metrics, default=valid_defaults)
            if st.button("Save Layout"):
                if save_user_preferences(selected_kpis): st.success("Saved!"); time.sleep(0.5); st.rerun()
        
        if selected_kpis:
            cols = st.columns(4)
            for i, metric in enumerate(selected_kpis):
                m_df = df[df['metric'] == metric].sort_values(by='Date')
                if m_df.empty: continue
                curr_row = m_df.iloc[-1]
                curr_val, curr_unit = curr_row['value'], curr_row['unit']
                
                if len(m_df) > 1:
                    prev_row = m_df.iloc[-2]
                    prev_val = prev_row['value']
                    diff = curr_val - prev_val
                    pct = (diff / prev_val) * 100
                    time_str = get_time_diff(curr_row['Date'], prev_row['Date'])
                    display_val = f"{prev_val} → {curr_val}"
                    delta_str = f"{diff:+.1f} ({pct:+.1f}%) in {time_str}"
                else:
                    display_val = f"{curr_val}"
                    delta_str = "First Record"

                with cols[i % 4]: st.metric(label=metric, value=display_val + f" {curr_unit}", delta=delta_str)
        
        st.markdown("---")
        st.subheader("Deep Dive")
        sel = st.selectbox("Select Metric", available_metrics)
        chart_df = df[df['metric']==sel]
        fig = px.line(chart_df, x="Date", y="value", markers=True, title=f"{sel} History")
        st.plotly_chart(fig, use_container_width=True)

with tabs[1]:
    st.header("User Profile")
    st.write("The AI Coach uses this data to customize your protocol.")
    
    current_profile = get_user_profile()
    
    with st.form("profile_form"):
        c1, c2 = st.columns(2)
        with c1:
            p_age = st.number_input("Age", value=int(current_profile.get('age', 25)), min_value=18, max_value=100)
            p_weight = st.number_input("Current Weight (lbs)", value=float(current_profile.get('weight', 180.0)))
            p_gender = st.selectbox("Gender", ["Male", "Female"], index=0 if current_profile.get('gender') == "Male" else 1)
        with c2:
            p_height = st.text_input("Height (e.g. 5'10)", value=current_profile.get('height', ""))
            p_goal = st.selectbox("Primary Goal", 
                                ["Optimization / Longevity", 
                                 "Muscle Gain / Hypertrophy", 
                                 "Fat Loss", 
                                 "Cognitive Performance", 
                                 "Libido / Hormone Health"],
                                index=0)
            
        if st.form_submit_button("Save Profile"):
            save_user_profile(p_age, p_height, p_gender, p_goal, p_weight)
            ts = str(int(time.time()))
            table.put_item(Item={'user_id': st.session_state.username, 'record_id': f"Weight_{ts}", 'metric': "Body Weight", 'value': str(p_weight), 'unit': 'lbs', 'upload_timestamp': ts, 'source_file': 'Profile_Update'})
            time.sleep(0.5)
            st.rerun()

with tabs[2]:
    st.header("🧠 Intelligence Center")
    if df.empty: st.warning("Upload data first.")
    else:
        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("Correlation Engine")
            all_m = df['metric'].unique()
            m1 = st.selectbox("Left Axis", all_m, index=0)
            m2 = st.selectbox("Right Axis", all_m, index=1 if len(all_m)>1 else 0)
            d1 = df[df['metric'] == m1].sort_values('Date')
            d2 = df[df['metric'] == m2].sort_values('Date')
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=d1['Date'], y=d1['value'], name=m1, mode='lines+markers'))
            fig.add_trace(go.Scatter(x=d2['Date'], y=d2['value'], name=m2, mode='lines+markers', yaxis='y2'))
            fig.update_layout(title=f"{m1} vs {m2}", yaxis=dict(title=m1), yaxis2=dict(title=m2, overlaying='y', side='right'), legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            st.subheader("AI Protocol Coach")
            st.info("Uses your Profile, Labs, and Stack for analysis.")
            if st.button("⚡ Generate Full Audit", type="primary"):
                with st.spinner("Analyzing biochemistry & goals..."):
                    try: stack = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                    except: stack = []
                    
                    profile = get_user_profile()
                    if not profile: st.warning("Tip: Fill out the 'Profile' tab for better results.")
                    
                    advice = run_ai_coach(df, stack, profile)
                    st.markdown(advice)

with tabs[3]:
    st.header("Upload")
    files = st.file_uploader("PDF/CSV", accept_multiple_files=True)
    if files and st.button("Upload"):
        for f in files: s3.put_object(Bucket=BUCKET_NAME, Key=f"uploads/{st.session_state.username}/{f.name}", Body=f.getvalue())
        st.success("Uploaded. Processing...")

with tabs[4]:
    st.header("Stack")
    c1, c2 = st.columns([2,1])
    with c2:
        with st.form("add"):
            n = st.text_input("Name"); d = st.text_input("Dose"); f = st.selectbox("Freq", ["Daily", "AM/PM", "Weekly"])
            if st.form_submit_button("Add"): supp_table.put_item(Item={'user_id': st.session_state.username, 'item_name': n, 'dosage': d, 'frequency': f}); st.rerun()
    with c1:
        try: 
            items = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
            if items: st.dataframe(pd.DataFrame(items)[['item_name', 'dosage', 'frequency']], use_container_width=True)
        except: pass

with tabs[5]:
    st.header("Coaching")
    role = st.radio("Mode", ["Client", "Coach"])
    if role == "Client":
        coach = st.text_input("Coach Email")
        if st.button("Link"): rel_table.put_item(Item={'coach_id': coach, 'client_id': st.session_state.username}); st.success("Linked!")
    else:
        st.info("Coach Dashboard active.")