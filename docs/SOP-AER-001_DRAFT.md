# SOP-AER-001 — Aeration Blower Failure Response

**DRAFT FOR REVIEW — NOT APPROVED**

Generated from `edge_intelligence/sop_documents/SOP-AER-001.json`. All limits are demonstration values until an authorized plant engineer validates them.

SOP-AER-001: Aeration Blower Failure Response
Revision 0 — DRAFT FOR REVIEW — NOT APPROVED
This draft was written for the OpsLens demonstration. It is not an approved plant procedure.
All limits are demonstration values until validated by an authorized plant engineer.
1. Purpose
Give the operator a structured response when an aeration blower fails or loses capacity and dissolved oxygen falls.
2. Scope
Aeration blowers B-1, B-2 and B-3, the air header, and the aeration tanks.
3. Alarm conditions (demonstration values)
Dissolved oxygen below 1.0 mg/L (normal 1.5 to 3.0 mg/L).
Blower airflow below 2,000 Nm³/h (normal 2,800 to 3,400 Nm³/h).
Air header pressure below 380 mbar (normal 480 to 560 mbar).
Blower motor current at 78 A overload trip (normal 58 to 66 A).
4. Symptoms and observations
A blower shows stopped or faulted. Airflow and header pressure fall together. Dissolved oxygen falls over the following minutes. Effluent ammonia may rise later.

5. Procedure: blower failure response
1. Acknowledge the alarm and confirm dissolved oxygen, airflow and header pressure on the HMI.
2. Check which blowers are running and read any fault message on the blower panel.
3. Check whether the standby blower is available and whether its selector is in the correct position.
4. Inspect the failed blower from a safe distance for abnormal noise, heat, smell or a damaged belt or coupling.
5. Do not reset a tripped blower until the cause is identified and an authorized person agrees.
6. Follow the site procedure and manufacturer's start sequence before starting any blower.
7. Record the times, readings and actions in the shift log.
6. Equipment inspection considerations
Check the inlet filter, discharge valve position, belt or coupling condition, and motor temperature. Use SOP-GEN-001 for lockout before any hands-on work.
7. Safety precautions
Blowers restart automatically in some modes. Treat them as live until locked out. Wear hearing protection near running blowers.

8. Escalation criteria
Escalate to the shift supervisor if dissolved oxygen stays below 1.0 mg/L, or if no standby capacity is available.
Escalate to the plant manager if effluent ammonia approaches the 5 mg/L permit value. Use SOP-GEN-004 for permit reporting.
9. Recovery verification
Dissolved oxygen is back within 1.5 to 3.0 mg/L and stable.
Airflow is back within 2,800 to 3,400 Nm³/h and header pressure within 480 to 560 mbar.
The alarm inputs have returned to normal at a later timestamp than the alarm.
10. Related procedures
SOP-GEN-001 Lockout/Tagout. SOP-BIO-001 Activated Sludge Foam Control. SOP-GEN-004 Permit Exceedance.
11. References and approval status
Status: DRAFT FOR REVIEW — NOT APPROVED. Approved by: not approved. Review date: not set.
