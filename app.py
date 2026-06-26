import streamlit as st
import requests

API = "http://localhost:8000"

st.set_page_config(page_title="HomeSite Claims Agent", page_icon="🏠", layout="centered")
st.title("🏠 HomeSite Claims Agent")
st.caption("AI-powered insurance claim verification")

tab1, tab2, tab3 = st.tabs(["Submit Claim", "Add Evidence", "Check Status"])


def show_result(data):
    """Renders the claim result with cost breakdown."""
    status   = data.get("status")
    decision = data.get("decision", "")

    if status == "waiting_for_evidence":
        st.warning("⏸️ More information needed")
        st.info(data.get("message", ""))
        st.caption("Go to the **Add Evidence** tab to continue.")
        return

    final = data.get("final_answer", "")

    if decision == "pass":
        st.success(final)
        # cost summary cards
        est  = data.get("estimated_cost")
        ded  = data.get("deductible")
        pays = data.get("approved_amount")
        if est is not None:
            c1, c2, c3 = st.columns(3)
            c1.metric("Repair Estimate", f"${est:,.0f}")
            c2.metric("Your Deductible", f"${ded:,.0f}")
            c3.metric("HomeSite Pays",   f"${pays:,.0f}", delta=f"+${pays:,.0f}")

    elif decision == "fail":
        st.error(final)
        est = data.get("estimated_cost")
        ded = data.get("deductible")
        if est is not None:
            c1, c2 = st.columns(2)
            c1.metric("Repair Estimate",  f"${est:,.0f}")
            c2.metric("Your Deductible",  f"${ded:,.0f}")

    elif decision == "flag_fraud":
        st.error(final)

    else:
        st.warning(final)


# ── TAB 1: Submit ─────────────────────────────────────────────────────────────
with tab1:
    st.subheader("Submit a New Claim")

    with st.form("submit_claim"):
        claim_id    = st.text_input("Claim ID",   value="CLM-001", placeholder="e.g. CLM-001")
        policy_id   = st.text_input("Policy ID",  value="POL-001", placeholder="e.g. POL-001")
        description = st.text_area("What happened?", placeholder="Describe the incident in detail...")
        uploaded    = st.file_uploader(
            "Upload photo or video of the damage",
            type=["jpg", "jpeg", "png", "heic", "mp4", "mov"],
            help="Photos or videos are both accepted."
        )
        submitted = st.form_submit_button("Submit Claim", use_container_width=True, type="primary")

    if submitted:
        if not claim_id or not description or not uploaded:
            st.warning("Please fill in all fields and upload a file.")
        else:
            with st.spinner("Processing your claim through all pipeline stages..."):
                resp = requests.post(
                    f"{API}/claims/submit",
                    data={
                        "claim_id":    claim_id,
                        "policy_id":   policy_id,
                        "description": description,
                    },
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
                )

            if resp.status_code == 200:
                show_result(resp.json())
            else:
                st.error(f"Error {resp.status_code}")
                st.json(resp.json())


# ── TAB 2: Add Evidence ───────────────────────────────────────────────────────
with tab2:
    st.subheader("Add Evidence to a Waiting Claim")
    st.caption("Use this if your claim was paused and you were asked for more information.")

    with st.form("submit_evidence"):
        ev_claim_id  = st.text_input("Claim ID", placeholder="e.g. CLM-001")
        extra_desc   = st.text_area("Additional details", placeholder="Police report number, repair shop estimate, additional context...")
        ev_submitted = st.form_submit_button("Submit Evidence", use_container_width=True, type="primary")

    if ev_submitted:
        if not ev_claim_id or not extra_desc:
            st.warning("Please fill in both fields.")
        else:
            with st.spinner("Resuming claim processing..."):
                resp = requests.post(
                    f"{API}/claims/{ev_claim_id}/evidence",
                    data={"extra_description": extra_desc}
                )

            if resp.status_code == 404:
                st.error(f"Claim `{ev_claim_id}` not found.")
            elif resp.status_code == 200:
                show_result(resp.json())
            else:
                st.json(resp.json())


# ── TAB 3: Check Status ───────────────────────────────────────────────────────
with tab3:
    st.subheader("Check Claim Status")

    with st.form("check_status"):
        st_claim_id  = st.text_input("Claim ID", placeholder="e.g. CLM-001")
        st_submitted = st.form_submit_button("Check Status", use_container_width=True)

    if st_submitted:
        if not st_claim_id:
            st.warning("Please enter a Claim ID.")
        else:
            resp = requests.get(f"{API}/claims/{st_claim_id}/status")

            if resp.status_code == 404:
                st.error(f"Claim `{st_claim_id}` not found.")
            else:
                data = resp.json()

                st.markdown("### Claim Summary")
                col1, col2, col3 = st.columns(3)
                col1.metric("Status",        data.get("status", "—").upper())
                col2.metric("Decision",      data.get("decision") or "pending")
                col3.metric("Info Requests", data.get("info_requests", 0))

                est  = data.get("estimated_cost")
                ded  = data.get("deductible")
                pays = data.get("approved_amount")
                if est is not None:
                    st.markdown("### Cost Breakdown")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Repair Estimate", f"${est:,.0f}")
                    c2.metric("Deductible",      f"${ded:,.0f}" if ded else "—")
                    c3.metric("Payout",          f"${pays:,.0f}" if pays else "$0")

                if data.get("final_answer"):
                    st.markdown("### Decision Details")
                    st.info(data.get("final_answer"))

                if data.get("waiting_at"):
                    st.warning(f"⏸️ Waiting at: `{data['waiting_at']}`")
