# Enterprise Resource Planning System Proposal

## National Board for Technology Incubation (NBTI)

**Proposal Reference:** DOTMAC/NBTI/2026/001
**Date:** February 2026
**Version:** 1.0

---

# Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Understanding NBTI](#2-understanding-nbti)
3. [Proposed Solution Overview](#3-proposed-solution-overview)
4. [Detailed Module Specifications](#4-detailed-module-specifications)
5. [Technical Architecture](#5-technical-architecture)
6. [Implementation Approach](#6-implementation-approach)
7. [Project Timeline](#7-project-timeline)
8. [Training & Change Management](#8-training--change-management)
9. [Support & Maintenance](#9-support--maintenance)
10. [Investment Summary](#10-investment-summary)
11. [Why DotMac ERP](#11-why-dotmac-erp)
12. [Appendices](#12-appendices)

---

# 1. Executive Summary

## 1.1 Introduction

DotMac Solutions is pleased to present this proposal for a comprehensive Enterprise Resource Planning (ERP) system tailored specifically for the National Board for Technology Incubation (NBTI).

This solution addresses NBTI's unique position as both a federal government agency requiring standard public sector financial management AND a technology incubation organization with specialized operational needs.

## 1.2 Proposed Solution

We propose a **fully integrated, cloud-ready ERP system** comprising:

| Category | Modules |
|----------|---------|
| **Financial Management** | General Ledger (IPSAS-compliant), Budget Management, Treasury Operations |
| **Procurement** | e-Procurement, Vendor Management, Contract Management |
| **Human Resources** | Personnel Management, Payroll, Leave, Performance |
| **Asset Management** | Fixed Assets, Equipment Tracking, Facility Management |
| **Incubation Operations** | Centre Management, Incubatee Tracking, Program Management, Mentorship, Impact Measurement |
| **Grants & Funding** | Grant Programs, Disbursement, Beneficiary Reporting |
| **Reporting & Analytics** | IPSAS Statements, Budget Execution, Dashboards, Custom Reports |

## 1.3 Key Benefits

- **IPSAS Compliance:** Full compliance with International Public Sector Accounting Standards
- **Public Procurement Act 2007 Compliance:** Built-in threshold controls and approval workflows
- **TSA/GIFMIS Ready:** Designed for integration with federal treasury systems
- **Mission-Specific:** Dedicated modules for incubation centre operations
- **Modern & Secure:** Web-based, role-based access, full audit trail
- **Scalable:** Supports all 40+ Technology Incubation Centres nationwide

## 1.4 Investment Overview

| Phase | Duration | Investment |
|-------|----------|------------|
| Phase 1: Core Government Functions | 12 weeks | ₦XX,XXX,XXX |
| Phase 2: Incubation Operations | 8 weeks | ₦XX,XXX,XXX |
| Phase 3: Advanced Features | 6 weeks | ₦XX,XXX,XXX |
| **Total Implementation** | **26 weeks** | **₦XX,XXX,XXX** |
| Annual Support & Maintenance | Ongoing | ₦X,XXX,XXX/year |

---

# 2. Understanding NBTI

## 2.1 About NBTI

The National Board for Technology Incubation (NBTI) is a federal government agency under the Federal Ministry of Science, Technology and Innovation. Established to promote the growth of indigenous technology-based enterprises, NBTI operates Technology Incubation Centres (TICs) across Nigeria.

## 2.2 NBTI's Mandate

1. Nurture technology-based small and medium enterprises (SMEs)
2. Provide workspace, equipment, and support services to entrepreneurs
3. Facilitate technology transfer and commercialization
4. Create employment through entrepreneurship development
5. Promote innovation and indigenous technology development

## 2.3 Current Operational Structure

| Component | Scope |
|-----------|-------|
| Headquarters | Abuja |
| Technology Incubation Centres | 40+ nationwide |
| Staff Strength | XXX employees |
| Annual Budget | ₦X.X billion |
| Active Incubatees | X,XXX+ entrepreneurs |
| Graduate Companies | XXX+ successful exits |

## 2.4 Key Challenges Addressed

| Challenge | Impact | Our Solution |
|-----------|--------|--------------|
| Manual financial processes | Delayed reporting, errors | Automated IPSAS-compliant GL |
| No integrated budget control | Overspending risk | Real-time budget enforcement |
| Paper-based procurement | Delays, compliance risk | e-Procurement with threshold controls |
| Fragmented incubatee data | No impact measurement | Centralized incubation management |
| No equipment tracking | Asset loss, underutilization | Facility & asset management |
| Disconnected TICs | Inconsistent operations | Unified multi-location system |

## 2.5 Regulatory Requirements

The proposed system is designed to comply with:

- **International Public Sector Accounting Standards (IPSAS)**
- **Public Procurement Act 2007**
- **Financial Regulations 2009**
- **Treasury Single Account (TSA) Guidelines**
- **GIFMIS Integration Requirements**
- **Bureau of Public Procurement (BPP) Guidelines**
- **Accountant General's Chart of Accounts**

---

# 3. Proposed Solution Overview

## 3.1 Solution Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    NBTI ERP SYSTEM                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   FINANCE    │  │ PROCUREMENT  │  │   HUMAN      │          │
│  │              │  │              │  │  RESOURCES   │          │
│  │ • General    │  │ • Requisition│  │              │          │
│  │   Ledger     │  │ • Quotations │  │ • Personnel  │          │
│  │ • Budget     │  │ • Evaluation │  │ • Payroll    │          │
│  │ • Treasury   │  │ • Contracts  │  │ • Leave      │          │
│  │ • Payments   │  │ • Vendors    │  │ • Training   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   ASSETS     │  │ INCUBATION   │  │   GRANTS     │          │
│  │              │  │  OPERATIONS  │  │              │          │
│  │ • Fixed      │  │              │  │ • Programs   │          │
│  │   Assets     │  │ • Centres    │  │ • Application│          │
│  │ • Equipment  │  │ • Incubatees │  │ • Disburse   │          │
│  │ • Facilities │  │ • Programs   │  │ • Monitoring │          │
│  │ • Booking    │  │ • Mentorship │  │ • Reporting  │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              REPORTING & ANALYTICS                       │   │
│  │  • IPSAS Financial Statements  • Budget Execution       │   │
│  │  • Procurement Reports         • Impact Dashboards      │   │
│  │  • Monthly Treasury Returns    • Custom Reports         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  INTEGRATION LAYER: TSA | GIFMIS | IPPIS | Remita | Banks      │
└─────────────────────────────────────────────────────────────────┘
```

## 3.2 Module Summary

### Financial Management Suite

| Module | Description |
|--------|-------------|
| **General Ledger** | IPSAS-compliant fund accounting with appropriation control |
| **Budget Management** | Appropriation, allotment, commitment, and expenditure tracking |
| **Accounts Payable** | Vendor payments, WHT deduction, Remita integration |
| **Accounts Receivable** | Revenue collection, receipting, TSA remittance |
| **Treasury Operations** | Cash management, bank reconciliation, fund transfers |
| **Fixed Assets** | Asset register, depreciation, disposal tracking |

### Procurement Suite

| Module | Description |
|--------|-------------|
| **Procurement Planning** | Annual procurement plan aligned with budget |
| **e-Requisition** | Internal purchase requests with approval workflow |
| **Quotation Management** | RFQ, bid receipt, evaluation, comparison |
| **Contract Management** | Award, execution, amendments, completion |
| **Vendor Management** | Registration, prequalification, performance tracking |

### Human Resources Suite

| Module | Description |
|--------|-------------|
| **Personnel Management** | Employee records, organizational structure |
| **Payroll Processing** | Salary computation, PAYE, pension, NHF |
| **Leave Management** | Leave types, applications, approvals, balances |
| **Training & Development** | Training programs, attendance, certifications |
| **Performance Management** | Appraisals, KPIs, evaluations |

### Incubation Operations Suite (NBTI-Specific)

| Module | Description |
|--------|-------------|
| **Centre Management** | TIC profiles, capacity, staffing, performance |
| **Incubatee Management** | Entrepreneur profiles, company tracking, lifecycle |
| **Program Management** | Incubation programs, cohorts, curricula |
| **Mentorship Tracking** | Mentor registry, matching, session logging |
| **Facility Management** | Equipment, labs, workspace booking |
| **Impact Measurement** | Jobs created, revenue, graduations, success metrics |

### Grants & Funding Suite

| Module | Description |
|--------|-------------|
| **Grant Programs** | Funding schemes, eligibility criteria, timelines |
| **Application Processing** | Online applications, evaluation, selection |
| **Disbursement** | Payment processing, tranches, conditions |
| **Monitoring & Reporting** | Fund utilization, milestone tracking, impact |

---

# 4. Detailed Module Specifications

## 4.1 Financial Management

### 4.1.1 General Ledger (IPSAS-Compliant)

**Overview:**
A comprehensive general ledger system designed specifically for Nigerian public sector accounting, fully compliant with IPSAS cash and accrual basis requirements.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Fund Accounting** | Separate tracking for Capital, Recurrent, Special, and Donor funds |
| **Chart of Accounts** | Pre-configured with Federal Government economic codes |
| **Segment Structure** | Admin → Economic → Fund → Functional → Program → Project |
| **Multi-Currency** | Support for foreign-denominated transactions |
| **Journal Processing** | Manual, recurring, reversing, and system-generated entries |
| **Period Control** | Open/close periods, prevent backdated entries |
| **Audit Trail** | Complete transaction history with user tracking |

**IPSAS Compliance Features:**

| IPSAS Standard | Implementation |
|----------------|----------------|
| IPSAS 1 | Presentation of Financial Statements |
| IPSAS 2 | Cash Flow Statements |
| IPSAS 3 | Accounting Policies, Changes, and Errors |
| IPSAS 9 | Revenue from Exchange Transactions |
| IPSAS 12 | Inventories |
| IPSAS 17 | Property, Plant, and Equipment |
| IPSAS 24 | Budget Information in Financial Statements |

**Outputs:**
- Statement of Financial Position
- Statement of Financial Performance
- Statement of Changes in Net Assets
- Cash Flow Statement
- Statement of Comparison of Budget and Actual Amounts
- Notes to Financial Statements

---

### 4.1.2 Budget Management

**Overview:**
Comprehensive budget management with hard controls to ensure compliance with appropriation limits and prevent overspending.

**Budget Lifecycle:**

```
Appropriation → Allotment → Commitment → Obligation → Expenditure
     ↓              ↓            ↓            ↓            ↓
  Annual        Quarterly    Purchase     Invoice      Payment
  Budget        Release      Order        Receipt      Made
```

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Budget Preparation** | Multi-level budget input by cost centre/program |
| **Appropriation Control** | Lock budgets to approved Appropriation Act amounts |
| **Allotment Management** | Quarterly/monthly fund releases |
| **Commitment Tracking** | Encumber funds when PO is raised |
| **Hard Budget Control** | Block transactions exceeding available budget |
| **Virement Processing** | Budget transfers with approval workflow |
| **Supplementary Budget** | Amendments during fiscal year |

**Budget Control Rules:**

| Transaction Type | Budget Check | Block if Exceeded |
|-----------------|--------------|-------------------|
| Purchase Requisition | Warning | No |
| Purchase Order | Hard Check | Yes |
| Invoice Receipt | Hard Check | Yes |
| Payment | Hard Check | Yes |
| Journal Entry | Hard Check | Configurable |

**Available Balance Calculation:**
```
Available Balance = Appropriation
                  - Commitments (Open POs)
                  - Obligations (Unpaid Invoices)
                  - Expenditure (Payments Made)
                  + Virement In
                  - Virement Out
```

**Reports:**
- Budget vs Actual (by fund, economic code, program)
- Commitment Register
- Available Balance Report
- Virement Report
- Budget Execution Summary
- Monthly Budget Performance

---

### 4.1.3 Accounts Payable

**Overview:**
Complete payables management from invoice receipt to payment, with integration to TSA and Remita for disbursements.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Invoice Processing** | Capture, verify, and post supplier invoices |
| **Three-Way Matching** | Match invoice to PO and goods receipt |
| **Approval Workflow** | Multi-level approval based on amount |
| **WHT Computation** | Automatic withholding tax calculation |
| **Payment Processing** | Batch payments via Remita |
| **Aging Analysis** | Track outstanding payables by age |

**Withholding Tax Integration:**

| Payment Type | WHT Rate | Handling |
|--------------|----------|----------|
| Contract | 5% | Auto-deduct, generate WHT credit note |
| Professional Services | 10% | Auto-deduct, generate WHT credit note |
| Rent | 10% | Auto-deduct, generate WHT credit note |
| Dividend | 10% | Auto-deduct, generate WHT credit note |

**Payment Methods:**
- Remita (Primary - TSA compliant)
- Direct bank transfer
- Cheque (with register)

---

### 4.1.4 Accounts Receivable

**Overview:**
Revenue management including appropriations-in-aid, fees, and other internally generated revenue with TSA remittance tracking.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Revenue Heads** | Configure revenue types per federal guidelines |
| **Invoice Generation** | Bill for services rendered |
| **Receipt Processing** | Issue receipts for payments received |
| **TSA Remittance** | Track remittance to Treasury Single Account |
| **Revenue Reconciliation** | Match collections to bank deposits |

**Revenue Types for NBTI:**
- Facility usage fees
- Training program fees
- Consultancy income
- Equipment rental
- Application fees
- Other internally generated revenue

---

### 4.1.5 Treasury Operations

**Overview:**
Cash management, bank account administration, and reconciliation functions.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Bank Account Management** | Multiple accounts, TSA sub-accounts |
| **Cash Position** | Real-time cash balance monitoring |
| **Bank Reconciliation** | Automated matching, exception handling |
| **Fund Transfers** | Inter-account transfers with approval |
| **Payment Scheduling** | Manage payment calendar |

---

### 4.1.6 Fixed Assets

**Overview:**
Complete fixed asset lifecycle management from acquisition to disposal.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Asset Register** | Comprehensive asset database |
| **Asset Categories** | Configure categories with depreciation rules |
| **Asset Acquisition** | Link to procurement/AP |
| **Depreciation** | Multiple methods (straight-line, reducing balance) |
| **Asset Transfers** | Move assets between locations/TICs |
| **Asset Disposal** | Retirement, sale, write-off |
| **Physical Verification** | Barcode/QR code support for stock-take |
| **Insurance Tracking** | Insurance details and renewals |

**Asset Classification:**
- Land and Buildings
- Plant and Machinery
- Motor Vehicles
- Furniture and Fittings
- Computer Equipment
- Laboratory Equipment
- Office Equipment

---

## 4.2 Procurement Management

### 4.2.1 Procurement Planning

**Overview:**
Annual procurement planning aligned with budget and regulatory requirements.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Annual Procurement Plan** | Plan all procurement for fiscal year |
| **Budget Linkage** | Link procurement items to budget lines |
| **Threshold Analysis** | Automatic categorization by value |
| **Timeline Planning** | Schedule procurement activities |
| **BPP Submission** | Generate format for BPP review |

**Procurement Plan Template:**

| Field | Description |
|-------|-------------|
| Item Description | What is being procured |
| Budget Line | Economic code |
| Estimated Value | Expected cost |
| Procurement Method | Open, Selective, Direct |
| Quarter | Planned procurement quarter |
| Approving Authority | Based on threshold |

---

### 4.2.2 e-Requisition

**Overview:**
Internal purchase request management with approval workflows.

**Workflow:**
```
Requester → Supervisor → Budget Officer → Procurement Unit
    ↓           ↓              ↓                ↓
  Create     Review        Verify           Process
  Request    & Approve     Budget          Requisition
```

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Requisition Creation** | User-friendly request form |
| **Item Catalog** | Pre-approved items with estimates |
| **Budget Check** | Real-time available balance verification |
| **Approval Routing** | Configurable approval chains |
| **Urgency Levels** | Normal, Urgent, Emergency |
| **Attachment Support** | Supporting documents |
| **Status Tracking** | Real-time requisition status |

---

### 4.2.3 Quotation Management

**Overview:**
Request for Quotation (RFQ), bid management, and evaluation.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **RFQ Generation** | Create and distribute RFQs |
| **Vendor Invitation** | Select vendors from registry |
| **Bid Receipt** | Record quotations received |
| **Bid Opening** | Formal bid opening with timestamp |
| **Evaluation Matrix** | Scoring criteria configuration |
| **Comparative Analysis** | Side-by-side bid comparison |
| **Recommendation** | Generate evaluation report |

**Evaluation Criteria (Configurable):**
- Price (e.g., 60%)
- Technical Capability (e.g., 20%)
- Delivery Timeline (e.g., 10%)
- Past Performance (e.g., 10%)

---

### 4.2.4 Approval Thresholds

**Overview:**
Enforcement of Public Procurement Act 2007 thresholds.

**Threshold Configuration:**

| Value Range (₦) | Procurement Method | Approving Authority |
|-----------------|-------------------|---------------------|
| < 2,500,000 | Direct Procurement | Accounting Officer |
| 2,500,000 - 50,000,000 | Selective Tendering | Tenders Board |
| 50,000,000 - 1,000,000,000 | Open Competitive | Ministerial Tenders Board |
| > 1,000,000,000 | Open Competitive | Federal Executive Council |

**System Enforcement:**
- Automatic routing based on value
- Mandatory BPP clearance for applicable values
- Certificate of No Objection tracking
- Due process compliance checks

---

### 4.2.5 Contract Management

**Overview:**
Contract lifecycle management from award to completion.

**Contract Workflow:**
```
Award → Execution → Performance → Completion → Closure
  ↓         ↓           ↓            ↓           ↓
Letter   Signing    Milestones    Delivery    Retention
of Award            & Payments    & Handover  Release
```

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Contract Register** | Centralized contract database |
| **Award Letters** | Generate award notification |
| **Contract Documents** | Store signed contracts |
| **Milestone Tracking** | Define and track deliverables |
| **Payment Schedule** | Planned vs actual payments |
| **Variation Management** | Contract amendments with approval |
| **Performance Evaluation** | Vendor performance scoring |
| **Completion Certificate** | Formal contract closure |

---

### 4.2.6 Vendor Management

**Overview:**
Comprehensive vendor registry and performance management.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Vendor Registration** | Self-service registration portal |
| **Prequalification** | Eligibility assessment |
| **Category Assignment** | Vendor classification |
| **Document Management** | Certificates, tax clearance, etc. |
| **Blacklist Management** | Maintain list of debarred vendors |
| **Performance History** | Track vendor performance |
| **Vendor Ratings** | Score based on delivery, quality |

**Required Vendor Documents:**
- Certificate of Incorporation
- Tax Clearance Certificate
- VAT Registration
- Pension Compliance
- ITF Compliance
- NSITF Compliance
- Audited Financial Statements
- Evidence of Similar Contracts

---

## 4.3 Human Resources Management

### 4.3.1 Personnel Management

**Overview:**
Comprehensive employee information management aligned with federal civil service structure.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Employee Records** | Complete biographical and employment data |
| **Organizational Structure** | Departments, units, reporting lines |
| **Grade Levels** | GL 01-17 structure |
| **Service History** | Postings, promotions, discipline |
| **Qualifications** | Academic and professional credentials |
| **Document Repository** | Employee documents storage |

**Employee Data Categories:**
- Personal Information
- Contact Details
- Next of Kin
- Employment Details
- Bank Information
- Qualifications
- Service Record
- Training History

**IPPIS Integration:**
- Employee data synchronization
- Payroll data exchange
- Biometric verification support

---

### 4.3.2 Payroll Processing

**Overview:**
Salary processing for civil servants with statutory deductions and allowances.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Salary Structure** | CONPASS/CONTISS salary scales |
| **Allowances** | Configure various allowances |
| **Deductions** | PAYE, Pension, NHF, Loans, etc. |
| **Payroll Run** | Monthly payroll processing |
| **Payslip Generation** | Individual pay statements |
| **Bank Schedule** | Payment file for banks |
| **Statutory Returns** | PAYE, Pension, NHF reports |

**Standard Allowances:**
- Rent Subsidy
- Transport Allowance
- Meal Subsidy
- Utility Allowance
- Domestic Servant Allowance
- Leave Grant
- Responsibility Allowance

**Statutory Deductions:**
- PAYE (as per tax table)
- Pension (8% employee, 10% employer)
- National Housing Fund (2.5%)
- Union Dues (where applicable)

**IPPIS Compatibility:**
- Export format compatible with IPPIS
- Variance reconciliation
- Exception handling

---

### 4.3.3 Leave Management

**Overview:**
Leave administration for civil servants per public service rules.

**Leave Types:**

| Leave Type | Entitlement | Description |
|------------|-------------|-------------|
| Annual Leave | 30 days | Based on grade level |
| Casual Leave | 10 days | Short-term absence |
| Sick Leave | Per medical certificate | With documentation |
| Maternity Leave | 16 weeks | Female employees |
| Paternity Leave | 10 days | Male employees |
| Study Leave | As approved | With/without pay |
| Examination Leave | As needed | For approved exams |

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Leave Request** | Online application |
| **Approval Workflow** | Supervisor → HR |
| **Balance Tracking** | Real-time leave balances |
| **Leave Calendar** | Team leave visibility |
| **Relief Assignment** | Handover management |
| **Public Holidays** | Holiday calendar |

---

### 4.3.4 Training & Development

**Overview:**
Staff training and capacity building management.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Training Calendar** | Annual training schedule |
| **Nomination** | Training nominations |
| **Approval Workflow** | Budget and need verification |
| **Attendance Tracking** | Training participation |
| **Certification** | Certificate issuance |
| **Training Report** | Effectiveness assessment |

---

### 4.3.5 Performance Management

**Overview:**
Annual Performance Evaluation Review (APER) system for civil servants.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **APER Form** | Digital performance evaluation form |
| **Target Setting** | Annual objectives |
| **Mid-Year Review** | Interim assessment |
| **Annual Review** | Year-end evaluation |
| **Rating System** | Outstanding to Unsatisfactory scale |
| **Review Workflow** | Self → Supervisor → Counter-signing |

---

## 4.4 Incubation Operations (NBTI-Specific)

### 4.4.1 Centre Management

**Overview:**
Manage the network of Technology Incubation Centres (TICs) across Nigeria.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Centre Profile** | TIC details, location, capacity |
| **Centre Manager** | Staff assignment |
| **Facility Inventory** | Equipment and space listing |
| **Capacity Status** | Occupancy tracking |
| **Centre Dashboard** | KPIs per TIC |
| **Comparative Analysis** | Cross-TIC performance |

**Centre Information:**

| Field | Description |
|-------|-------------|
| Centre Name | Official name |
| Location | State, address, coordinates |
| Year Established | Founding year |
| Total Capacity | Number of incubatee slots |
| Current Occupancy | Active incubatees |
| Centre Manager | Responsible officer |
| Contact Details | Phone, email |
| Facilities | Labs, workspace, equipment |

**Centre Performance Metrics:**
- Occupancy rate
- Incubatee success rate
- Jobs created
- Revenue generated by incubatees
- Graduation rate
- Equipment utilization

---

### 4.4.2 Incubatee Management

**Overview:**
Complete lifecycle management of entrepreneurs and companies being incubated.

**Incubatee Lifecycle:**
```
Application → Selection → Admission → Incubation → Graduation
     ↓            ↓           ↓            ↓            ↓
  Submit      Evaluate      Onboard     Support       Exit
  Application  & Select     & Assign    & Monitor    & Track
```

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Online Application** | Web-based application portal |
| **Application Evaluation** | Scoring and selection process |
| **Incubatee Profile** | Company and founder details |
| **Admission Processing** | Agreement, space assignment |
| **Progress Tracking** | Milestone monitoring |
| **Support Services** | Services provided tracking |
| **Graduation Management** | Exit criteria, certification |
| **Alumni Tracking** | Post-graduation monitoring |

**Incubatee Profile Fields:**

| Category | Fields |
|----------|--------|
| Founder Information | Name, contact, background, qualifications |
| Company Information | Name, registration, sector, stage |
| Product/Service | Description, innovation, target market |
| Incubation Details | TIC assigned, space, start date |
| Milestones | Targets and achievements |
| Support Received | Mentorship, training, funding |

**Incubatee Stages:**
1. **Pre-Incubation:** Idea validation
2. **Early Stage:** Product development
3. **Growth Stage:** Market entry
4. **Maturation:** Scale-up
5. **Graduation:** Exit ready

---

### 4.4.3 Program Management

**Overview:**
Manage incubation programs, cohorts, and curricula.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Program Definition** | Create incubation programs |
| **Cohort Management** | Group incubatees by intake |
| **Curriculum Design** | Training modules and schedule |
| **Session Tracking** | Attendance and participation |
| **Progress Reports** | Cohort progress monitoring |
| **Program Evaluation** | Effectiveness assessment |

**Program Types:**
- Standard Incubation (12-24 months)
- Accelerator (3-6 months)
- Pre-Incubation (1-3 months)
- Virtual Incubation
- Sector-Specific Programs

**Program Structure:**

| Component | Description |
|-----------|-------------|
| Duration | Program length |
| Eligibility | Entry requirements |
| Curriculum | Training modules |
| Mentorship | Mentor assignments |
| Resources | Facilities, equipment |
| Funding | Grant components |
| Exit Criteria | Graduation requirements |

---

### 4.4.4 Mentorship Tracking

**Overview:**
Manage mentor-incubatee relationships and session tracking.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Mentor Registry** | Database of mentors |
| **Mentor Profile** | Expertise, availability, track record |
| **Matching Algorithm** | Match mentors to incubatees |
| **Session Scheduling** | Book mentorship sessions |
| **Session Logging** | Record session details |
| **Progress Notes** | Track advice and action items |
| **Mentor Evaluation** | Incubatee feedback on mentors |

**Mentor Categories:**
- Technical Mentors
- Business Mentors
- Industry Experts
- Financial Advisors
- Legal Advisors

**Session Types:**
- One-on-One Mentoring
- Group Sessions
- Workshop Facilitation
- Office Hours
- Virtual Sessions

---

### 4.4.5 Facility Management

**Overview:**
Manage TIC facilities, equipment, and bookings.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Space Management** | Offices, workstations, hot desks |
| **Equipment Registry** | Lab equipment, computers, tools |
| **Booking System** | Reserve facilities and equipment |
| **Availability Calendar** | Real-time availability view |
| **Usage Tracking** | Utilization monitoring |
| **Maintenance Scheduling** | Equipment maintenance |
| **Access Control** | Who can book what |

**Facility Categories:**

| Category | Examples |
|----------|----------|
| Workspaces | Private offices, shared space, hot desks |
| Meeting Rooms | Conference rooms, boardrooms |
| Labs | Fabrication lab, computer lab, testing lab |
| Equipment | 3D printers, CNC machines, testing equipment |
| Common Areas | Reception, cafeteria, lounge |

**Booking Rules:**
- Maximum booking duration
- Advance booking limits
- Cancellation policies
- Approval requirements for special equipment

---

### 4.4.6 Impact Measurement

**Overview:**
Track and report on the impact of NBTI's incubation activities.

**Key Metrics:**

| Metric Category | Specific Metrics |
|-----------------|------------------|
| **Job Creation** | Direct jobs, indirect jobs |
| **Revenue** | Incubatee revenue, exports |
| **Investment** | Funding raised by incubatees |
| **Graduation** | Success rate, exit quality |
| **Innovation** | Patents, products launched |
| **Sustainability** | Survival rate post-graduation |

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Metric Definition** | Configure metrics to track |
| **Data Collection** | Periodic incubatee surveys |
| **Automated Calculation** | Aggregate statistics |
| **Dashboard** | Visual KPI display |
| **Trend Analysis** | Performance over time |
| **Benchmarking** | Compare TICs, programs |
| **Report Generation** | Stakeholder reports |

**Impact Reports:**
- Quarterly Performance Report
- Annual Impact Report
- TIC Performance Ranking
- Sector Analysis
- State-by-State Impact
- SDG Alignment Report

---

## 4.5 Grants & Funding Management

### 4.5.1 Grant Programs

**Overview:**
Define and manage funding programs for incubatees.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Program Setup** | Create grant programs |
| **Eligibility Criteria** | Define who can apply |
| **Funding Limits** | Maximum amounts |
| **Timeline Management** | Application windows |
| **Budget Allocation** | Available funding |

**Grant Types:**
- Seed Grants
- Equipment Grants
- Prototype Development
- Market Access Support
- Training Grants
- Competition Prizes

---

### 4.5.2 Application Processing

**Overview:**
Manage grant applications from submission to decision.

**Workflow:**
```
Call for Applications → Submission → Screening → Evaluation → Selection → Award
```

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Application Portal** | Online submission |
| **Document Upload** | Supporting documents |
| **Screening** | Eligibility verification |
| **Evaluation Panel** | Reviewer assignment |
| **Scoring** | Evaluation criteria |
| **Selection Committee** | Final approval |
| **Award Notification** | Communicate decisions |

---

### 4.5.3 Disbursement

**Overview:**
Manage fund release to successful applicants.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Disbursement Schedule** | Tranche planning |
| **Milestone Linkage** | Release tied to achievements |
| **Payment Processing** | Integration with AP |
| **Acknowledgment** | Receipt confirmation |
| **Utilization Tracking** | How funds are used |

---

### 4.5.4 Monitoring & Reporting

**Overview:**
Track fund utilization and program impact.

**Key Features:**

| Feature | Description |
|---------|-------------|
| **Utilization Reports** | Beneficiary spending reports |
| **Milestone Verification** | Confirm achievement |
| **Site Visits** | Physical verification scheduling |
| **Impact Assessment** | Program effectiveness |
| **Donor Reporting** | Reports for funding partners |

---

## 4.6 Reporting & Analytics

### 4.6.1 IPSAS Financial Statements

| Report | Description |
|--------|-------------|
| Statement of Financial Position | Assets, liabilities, net assets |
| Statement of Financial Performance | Revenue and expenses |
| Statement of Changes in Net Assets | Equity movements |
| Cash Flow Statement | Cash receipts and payments |
| Budget vs Actual | Appropriation comparison |
| Notes to Financial Statements | Disclosures |

### 4.6.2 Management Reports

| Report | Description |
|--------|-------------|
| Budget Execution Report | Spending vs budget |
| Commitment Report | Outstanding commitments |
| Aging Reports | AR and AP aging |
| Cash Position | Daily/weekly cash status |
| Procurement Status | Pipeline and completion |
| Asset Register | Complete asset listing |

### 4.6.3 Statutory Reports

| Report | Description |
|--------|-------------|
| Monthly Treasury Returns | OAGF monthly report |
| Quarterly Budget Report | MDAs quarterly report |
| Annual Financial Statements | Year-end statements |
| PAYE Returns | Monthly/annual tax report |
| Pension Schedule | Contribution reports |

### 4.6.4 Incubation Reports

| Report | Description |
|--------|-------------|
| Incubatee Status Report | All incubatees and status |
| TIC Performance Dashboard | Per-centre metrics |
| Impact Summary | Jobs, revenue, exits |
| Program Effectiveness | Program success rates |
| Mentor Utilization | Mentorship activity |
| Facility Utilization | Space and equipment usage |

### 4.6.5 Executive Dashboards

**Director General Dashboard:**
- Budget execution summary
- Procurement pipeline
- TIC occupancy rates
- Impact metrics (jobs, revenue)
- Key alerts and notifications

**Centre Manager Dashboard:**
- Centre occupancy
- Incubatee progress
- Facility utilization
- Upcoming events
- Pending actions

---

# 5. Technical Architecture

## 5.1 Technology Stack

| Layer | Technology | Rationale |
|-------|------------|-----------|
| **Frontend** | HTML5, Jinja2, HTMX, TailwindCSS | Modern, responsive, accessible |
| **Backend** | Python, FastAPI | High performance, type-safe |
| **Database** | PostgreSQL | Enterprise-grade, ACID compliant |
| **Caching** | Redis | Session management, performance |
| **Task Queue** | Celery | Background job processing |
| **Reporting** | WeasyPrint, OpenPyXL | PDF and Excel generation |
| **Search** | PostgreSQL Full-Text | Document and record search |

## 5.2 Deployment Architecture

```
                         ┌─────────────────┐
                         │   Load Balancer │
                         │    (Nginx)      │
                         └────────┬────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
     ┌────────┴────────┐ ┌───────┴────────┐ ┌───────┴────────┐
     │   App Server 1  │ │  App Server 2  │ │  App Server 3  │
     │    (Gunicorn)   │ │   (Gunicorn)   │ │   (Gunicorn)   │
     └────────┬────────┘ └───────┬────────┘ └───────┬────────┘
              │                  │                   │
              └──────────────────┼───────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────┴────────┐    ┌────────┴────────┐    ┌────────┴────────┐
│   PostgreSQL    │    │     Redis       │    │  Celery Workers │
│   (Primary)     │    │    Cluster      │    │  (Background)   │
└────────┬────────┘    └─────────────────┘    └─────────────────┘
         │
┌────────┴────────┐
│   PostgreSQL    │
│   (Replica)     │
└─────────────────┘
```

## 5.3 Security Architecture

| Security Layer | Implementation |
|----------------|----------------|
| **Authentication** | Multi-factor authentication (MFA) |
| **Authorization** | Role-based access control (RBAC) |
| **Encryption** | TLS 1.3 in transit, AES-256 at rest |
| **Audit** | Complete audit trail of all transactions |
| **Session** | Secure session management, timeout |
| **Input Validation** | Server-side validation, SQL injection prevention |
| **Password** | Bcrypt hashing, complexity requirements |

## 5.4 Integration Interfaces

| System | Integration Method | Purpose |
|--------|-------------------|---------|
| **GIFMIS** | File export/API | Treasury integration |
| **IPPIS** | File export | Payroll synchronization |
| **Remita** | REST API | Payment processing |
| **TSA** | Via Remita | Revenue remittance |
| **Banks** | File export | Payment files |
| **BVN** | REST API | Identity verification |

## 5.5 Availability & Performance

| Metric | Target |
|--------|--------|
| Availability | 99.5% uptime |
| Response Time | < 2 seconds for standard pages |
| Concurrent Users | 500+ simultaneous users |
| Data Retention | 7 years online, unlimited archive |
| Backup | Daily automated backups |
| Recovery | 4-hour RTO, 1-hour RPO |

---

# 6. Implementation Approach

## 6.1 Methodology

We will employ an **Agile implementation methodology** with the following principles:

1. **Iterative Delivery:** Functional modules delivered incrementally
2. **User Involvement:** Key users engaged throughout
3. **Early Value:** Core functions delivered first
4. **Flexibility:** Adapt to feedback and changing requirements
5. **Quality Focus:** Continuous testing and validation

## 6.2 Implementation Phases

### Phase 1: Foundation (Weeks 1-12)

**Scope:**
- Financial Management (IPSAS GL, Budget, AP, AR, Treasury)
- Procurement Management
- Core HR & Payroll
- User Management & Security

**Deliverables:**
- Configured Chart of Accounts
- Budget structure loaded
- Procurement workflows operational
- Payroll processing ready
- Core users trained

### Phase 2: Incubation Operations (Weeks 13-20)

**Scope:**
- Centre Management
- Incubatee Management
- Program Management
- Mentorship Tracking
- Facility Management

**Deliverables:**
- All TICs configured
- Existing incubatees migrated
- Programs set up
- Mentor registry populated

### Phase 3: Advanced Features (Weeks 21-26)

**Scope:**
- Grants Management
- Impact Measurement
- Advanced Reporting
- Dashboards
- GIFMIS Integration

**Deliverables:**
- Grant programs operational
- Impact dashboards live
- All reports available
- External integrations tested

## 6.3 Implementation Activities

| Activity | Description |
|----------|-------------|
| **Project Initiation** | Kickoff, team formation, planning |
| **Requirements Validation** | Confirm detailed requirements |
| **System Configuration** | Set up modules per NBTI needs |
| **Data Migration** | Transfer existing data |
| **Integration Setup** | Connect to external systems |
| **User Acceptance Testing** | Validate with end users |
| **Training** | Train all user groups |
| **Parallel Run** | Run old and new systems together |
| **Go-Live** | Production deployment |
| **Stabilization** | Post-go-live support |

## 6.4 Data Migration Strategy

| Data Category | Source | Approach |
|---------------|--------|----------|
| Chart of Accounts | Federal template | Configure |
| Opening Balances | Prior year statements | Manual entry + verification |
| Employee Data | IPPIS/existing records | Clean and import |
| Asset Register | Physical verification | Fresh capture |
| Vendor Data | Existing records | Clean and import |
| Incubatee Data | TIC records | Collect and import |

---

# 7. Project Timeline

## 7.1 Summary Timeline

```
Month:    1    2    3    4    5    6    7
        ──────────────────────────────────────
Phase 1  ████████████████████████
Phase 2                          ████████████████
Phase 3                                      ████████████
```

## 7.2 Detailed Schedule

### Phase 1: Foundation (12 Weeks)

| Week | Activities |
|------|------------|
| 1-2 | Project initiation, team onboarding, infrastructure setup |
| 3-4 | Chart of Accounts configuration, budget structure setup |
| 5-6 | GL and budget management configuration |
| 7-8 | AP, AR, Treasury configuration |
| 9-10 | Procurement module configuration |
| 11 | HR and Payroll configuration |
| 12 | Phase 1 UAT and training |

### Phase 2: Incubation Operations (8 Weeks)

| Week | Activities |
|------|------------|
| 13-14 | Centre management setup, TIC data loading |
| 15-16 | Incubatee management, program setup |
| 17-18 | Mentorship and facility modules |
| 19-20 | Phase 2 UAT and training |

### Phase 3: Advanced Features (6 Weeks)

| Week | Activities |
|------|------------|
| 21-22 | Grants management, impact measurement |
| 23-24 | Reporting, dashboards, integration testing |
| 25 | Final UAT, parallel run |
| 26 | Go-Live and stabilization |

## 7.3 Key Milestones

| Milestone | Target Date | Description |
|-----------|-------------|-------------|
| M1: Project Kickoff | Week 1 | Project officially starts |
| M2: Infrastructure Ready | Week 2 | Systems configured |
| M3: Finance Module Go-Live | Week 8 | Core finance operational |
| M4: Procurement Go-Live | Week 10 | Procurement operational |
| M5: HR/Payroll Go-Live | Week 12 | HR and Payroll operational |
| M6: Incubation Module Go-Live | Week 20 | NBTI-specific modules live |
| M7: Full System Go-Live | Week 26 | All modules operational |

---

# 8. Training & Change Management

## 8.1 Training Approach

We will provide comprehensive training tailored to each user group:

| User Group | Training Focus | Duration |
|------------|----------------|----------|
| System Administrators | Full system configuration, security | 5 days |
| Finance Team | GL, Budget, AP, AR, Treasury | 5 days |
| Procurement Team | End-to-end procurement process | 3 days |
| HR Team | Personnel, payroll, leave | 3 days |
| TIC Managers | Centre operations, incubatee management | 3 days |
| General Users | Basic navigation, self-service | 1 day |
| Executives | Dashboards, reports, approvals | 0.5 day |

## 8.2 Training Materials

- User manuals (role-specific)
- Quick reference guides
- Video tutorials
- FAQs
- Online help within the system

## 8.3 Change Management

| Activity | Description |
|----------|-------------|
| **Stakeholder Engagement** | Regular communication with all stakeholders |
| **Champions Network** | Identify and train super-users in each unit |
| **Communication Plan** | Regular updates on project progress |
| **Resistance Management** | Address concerns proactively |
| **Benefits Realization** | Track and communicate quick wins |

---

# 9. Support & Maintenance

## 9.1 Post-Implementation Support

| Period | Support Level |
|--------|---------------|
| Weeks 1-4 post go-live | On-site dedicated support |
| Months 2-3 | On-site support (reduced) + remote |
| Month 4 onwards | Remote support + periodic visits |

## 9.2 Support Channels

| Channel | Availability | Response Time |
|---------|--------------|---------------|
| Phone Hotline | 8am - 6pm weekdays | Immediate |
| Email Support | 24/7 | 4 hours (business hours) |
| Support Portal | 24/7 | 8 hours |
| On-site (Critical) | As needed | 24 hours |

## 9.3 Issue Severity Levels

| Severity | Definition | Response | Resolution |
|----------|------------|----------|------------|
| Critical | System down, no workaround | 1 hour | 4 hours |
| High | Major function impaired | 4 hours | 24 hours |
| Medium | Function impaired, workaround exists | 8 hours | 72 hours |
| Low | Minor issue, cosmetic | 24 hours | 1 week |

## 9.4 Maintenance Services

| Service | Frequency |
|---------|-----------|
| Security patches | As released |
| Bug fixes | Monthly |
| Minor enhancements | Quarterly |
| Major upgrades | Annual |
| Database optimization | Monthly |
| Backup verification | Weekly |

---

# 10. Investment Summary

## 10.1 Implementation Costs

| Phase | Description | Investment (₦) |
|-------|-------------|----------------|
| **Phase 1** | Foundation (Finance, Procurement, HR) | XX,XXX,XXX |
| **Phase 2** | Incubation Operations | XX,XXX,XXX |
| **Phase 3** | Advanced Features | XX,XXX,XXX |
| **Subtotal Implementation** | | **XX,XXX,XXX** |

## 10.2 Cost Breakdown by Category

| Category | Description | Investment (₦) |
|----------|-------------|----------------|
| Software Licensing | Core ERP platform | XX,XXX,XXX |
| Customization | NBTI-specific modules | XX,XXX,XXX |
| Implementation Services | Configuration, migration, go-live | XX,XXX,XXX |
| Training | All user groups | X,XXX,XXX |
| Infrastructure | Hosting setup (if applicable) | X,XXX,XXX |
| Project Management | Dedicated PM | X,XXX,XXX |
| **Total Implementation** | | **XX,XXX,XXX** |

## 10.3 Recurring Costs

| Item | Annual Cost (₦) |
|------|-----------------|
| Software Maintenance | X,XXX,XXX |
| Technical Support | X,XXX,XXX |
| Hosting (if cloud) | X,XXX,XXX |
| **Total Annual** | **X,XXX,XXX** |

## 10.4 Payment Terms

| Milestone | Percentage | Amount (₦) |
|-----------|------------|------------|
| Contract Signing | 30% | XX,XXX,XXX |
| Phase 1 Completion | 30% | XX,XXX,XXX |
| Phase 2 Completion | 20% | XX,XXX,XXX |
| Full Go-Live | 15% | XX,XXX,XXX |
| 90 Days Post Go-Live | 5% | X,XXX,XXX |
| **Total** | **100%** | **XX,XXX,XXX** |

---

# 11. Why DotMac ERP

## 11.1 Our Differentiators

| Factor | Our Advantage |
|--------|---------------|
| **Purpose-Built for Nigeria** | Designed for Nigerian public sector, not adapted from foreign system |
| **IPSAS Native** | Built from ground up for public sector accounting |
| **Incubation Expertise** | Unique modules for NBTI's mission |
| **Modern Technology** | Web-based, mobile-friendly, secure |
| **Total Cost** | Fraction of international ERP costs |
| **Local Support** | Nigeria-based team, same timezone |
| **Rapid Implementation** | 6 months vs 2+ years for global ERPs |

## 11.2 Competitive Comparison

| Criteria | DotMac | Oracle | SAP | Local Legacy |
|----------|--------|--------|-----|--------------|
| IPSAS Compliance | ✅ Native | ⚠️ Add-on | ⚠️ Add-on | ❌ Limited |
| Incubation Module | ✅ Included | ❌ Custom | ❌ Custom | ❌ None |
| Implementation Time | 6 months | 24+ months | 24+ months | 12 months |
| Total Cost | ₦₦ | ₦₦₦₦₦ | ₦₦₦₦₦ | ₦₦ |
| Local Support | ✅ Full | ⚠️ Limited | ⚠️ Limited | ✅ Full |
| Modern UI | ✅ Yes | ⚠️ Dated | ⚠️ Dated | ❌ No |

## 11.3 Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Implementation delays | Fixed timeline with penalties |
| Budget overrun | Fixed price contract |
| Poor adoption | Comprehensive training and change management |
| Data loss | Robust backup and recovery |
| Vendor lock-in | Open data formats, documented APIs |
| Key person dependency | Knowledge transfer, documentation |

## 11.4 References

*Available upon request - similar implementations in comparable organizations.*

---

# 12. Appendices

## Appendix A: Detailed Feature List

*Comprehensive feature checklist for each module*

## Appendix B: Sample Reports

*Examples of key reports and dashboards*

## Appendix C: Technical Specifications

*Detailed technical requirements and architecture*

## Appendix D: Data Migration Templates

*Format for data migration from existing systems*

## Appendix E: Integration Specifications

*API documentation for external system integration*

## Appendix F: Service Level Agreement

*Detailed SLA terms and conditions*

## Appendix G: Company Profile

*DotMac Solutions company information and credentials*

## Appendix H: Team Profiles

*Key project team member qualifications*

---

# Contact Information

**DotMac Solutions**

For questions regarding this proposal, please contact:

**[Name]**
[Title]
Email: [email]
Phone: [phone]

---

*This proposal is valid for 90 days from the date of issue.*

*© 2026 DotMac Solutions. All rights reserved.*
