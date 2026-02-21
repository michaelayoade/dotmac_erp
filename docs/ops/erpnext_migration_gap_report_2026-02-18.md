# ERPNext Migration Gap Report

- Source dump: `/root/.dotmac/zenith statement/uba/Uba statement 2022-2025/.venv/20260216_163016-dotmac_frappe_cloud-database.sql.gz`
- Total ERPNext doctypes (tab tables): **992**
- Doctypes with data (INSERTs): **413**
- Doctypes covered by current sync code: **40**
- Doctypes with data not covered by sync: **373**

## Covered Doctypes
- Account
- Asset
- Asset Category
- Attendance
- Comment
- Communication
- Customer
- Department
- Designation
- Employee
- Employee Grade
- Employment Type
- Expense Claim
- Expense Claim Detail
- Expense Claim Type
- HD Ticket
- Issue
- Item
- Item Group
- Journal Entry
- Journal Entry Account
- Leave Allocation
- Leave Application
- Leave Type
- Material Request
- Material Request Item
- Payment Entry
- Payment Entry Reference
- Project
- Purchase Invoice
- Purchase Invoice Item
- Sales Invoice
- Sales Invoice Item
- Shift Type
- Stock Ledger Entry
- Supplier
- Task
- Timesheet
- Timesheet Detail
- Warehouse

## Unmigrated Doctypes (With Data) by Module
### Banking/Treasury (8)
- `Bank Transaction` (insert statements: 18)
- `Bank` (insert statements: 1)
- `Bank Account` (insert statements: 1)
- `Bank Names` (insert statements: 1)
- `Bank Statement Import` (insert statements: 1)
- `Payment Request` (insert statements: 1)
- `Unreconcile Payment` (insert statements: 1)
- `Unreconcile Payment Entries` (insert statements: 1)

### CRM (15)
- `CRM Communication Status` (insert statements: 1)
- `CRM Dashboard` (insert statements: 1)
- `CRM Deal Status` (insert statements: 1)
- `CRM Dropdown Item` (insert statements: 1)
- `CRM Fields Layout` (insert statements: 1)
- `CRM Form Script` (insert statements: 1)
- `CRM Industry` (insert statements: 1)
- `CRM Lead` (insert statements: 1)
- `CRM Lead Source` (insert statements: 1)
- `CRM Lead Status` (insert statements: 1)
- `CRM Lost Reason` (insert statements: 1)
- `CRM Note` (insert statements: 1)
- `CRM Organization` (insert statements: 1)
- `CRM Status Change Log` (insert statements: 1)
- `CRM View Settings` (insert statements: 1)

### Documents/Files (11)
- `Deleted Document` (insert statements: 179)
- `Communication Link` (insert statements: 40)
- `File` (insert statements: 24)
- `DocField` (insert statements: 6)
- `DocPerm` (insert statements: 1)
- `DocShare` (insert statements: 1)
- `DocType` (insert statements: 1)
- `DocType Action` (insert statements: 1)
- `DocType Link` (insert statements: 1)
- `DocType State` (insert statements: 1)
- `Document Share Key` (insert statements: 1)

### Finance-AP (8)
- `Purchase Invoice Advance` (insert statements: 1)
- `Purchase Order` (insert statements: 1)
- `Purchase Order Item` (insert statements: 1)
- `Purchase Receipt` (insert statements: 1)
- `Purchase Receipt Item` (insert statements: 1)
- `Supplier Group` (insert statements: 1)
- `Supplier Scorecard Standing` (insert statements: 1)
- `Supplier Scorecard Variable` (insert statements: 1)

### Finance-AR (10)
- `Sales Taxes and Charges` (insert statements: 12)
- `Payment Schedule` (insert statements: 10)
- `Quotation` (insert statements: 2)
- `Sales Invoice Advance` (insert statements: 2)
- `Sales Order` (insert statements: 2)
- `Advance Taxes and Charges` (insert statements: 1)
- `Customer Group` (insert statements: 1)
- `Quotation Item` (insert statements: 1)
- `Sales Order Item` (insert statements: 1)
- `Sales Taxes and Charges Template` (insert statements: 1)

### Finance-GL (8)
- `GL Entry` (insert statements: 110)
- `Payment Ledger Entry` (insert statements: 35)
- `Bank Transaction Payments` (insert statements: 10)
- `Cost Center` (insert statements: 1)
- `Fiscal Year` (insert statements: 1)
- `Fiscal Year Company` (insert statements: 1)
- `Mode of Payment` (insert statements: 1)
- `Mode of Payment Account` (insert statements: 1)

### HR Core (17)
- `Attendance Request` (insert statements: 1)
- `Branch` (insert statements: 1)
- `Branches` (insert statements: 1)
- `Department Approver` (insert statements: 1)
- `Designation Skill` (insert statements: 1)
- `Employee Boarding Activity` (insert statements: 1)
- `Employee Checkin` (insert statements: 1)
- `Employee Education` (insert statements: 1)
- `Employee External Work History` (insert statements: 1)
- `Employee Feedback Criteria` (insert statements: 1)
- `Employee Feedback Rating` (insert statements: 1)
- `Employee Onboarding Template` (insert statements: 1)
- `Holiday` (insert statements: 1)
- `Holiday List` (insert statements: 1)
- `Leave Ledger Entry` (insert statements: 1)
- `Leave Policy` (insert statements: 1)
- `Leave Policy Detail` (insert statements: 1)

### HR Payroll (6)
- `Salary Detail` (insert statements: 3)
- `Salary Slip` (insert statements: 3)
- `Payroll Period` (insert statements: 1)
- `Salary Component` (insert statements: 1)
- `Salary Structure` (insert statements: 1)
- `Salary Structure Assignment` (insert statements: 1)

### Inventory (23)
- `Stock Entry Detail` (insert statements: 5)
- `Stock Entry` (insert statements: 2)
- `Bin` (insert statements: 1)
- `Brand` (insert statements: 1)
- `Item Attribute` (insert statements: 1)
- `Item Attribute Value` (insert statements: 1)
- `Item Default` (insert statements: 1)
- `Item Maintenace` (insert statements: 1)
- `Item Price` (insert statements: 1)
- `Item Tax` (insert statements: 1)
- `Item Tax Template` (insert statements: 1)
- `Item Tax Template Detail` (insert statements: 1)
- `Serial No` (insert statements: 1)
- `Serial and Batch Bundle` (insert statements: 1)
- `Serial and Batch Entry` (insert statements: 1)
- `Stock Entry Type` (insert statements: 1)
- `Stock Reconciliation` (insert statements: 1)
- `Stock Reconciliation Item` (insert statements: 1)
- `UOM` (insert statements: 1)
- `UOM Category` (insert statements: 1)
- `UOM Conversion Detail` (insert statements: 1)
- `UOM Conversion Factor` (insert statements: 1)
- `Warehouse Type` (insert statements: 1)

### Other (232)
- `The Attendance` (insert statements: 7)
- `Contact` (insert statements: 3)
- `Dynamic Link` (insert statements: 3)
- `Block Module` (insert statements: 2)
- `Contact Email` (insert statements: 2)
- `Activity Type` (insert statements: 1)
- `Address` (insert statements: 1)
- `Address Template` (insert statements: 1)
- `Appraisal Template` (insert statements: 1)
- `Appraisal Template Goal` (insert statements: 1)
- `Asset Activity` (insert statements: 1)
- `Asset Category Account` (insert statements: 1)
- `Asset Movement` (insert statements: 1)
- `Asset Movement Item` (insert statements: 1)
- `Assignment Rule` (insert statements: 1)
- `Assignment Rule Day` (insert statements: 1)
- `Assignment Rule User` (insert statements: 1)
- `Auto Email Report` (insert statements: 1)
- `BTS Maintainance` (insert statements: 1)
- `Base Station` (insert statements: 1)
- `Base station Link` (insert statements: 1)
- `Bulk Transaction Log Detail` (insert statements: 1)
- `Cabinets` (insert statements: 1)
- `Cabinets Link` (insert statements: 1)
- `Campaign` (insert statements: 1)
- `Client Script` (insert statements: 1)
- `Color` (insert statements: 1)
- `Company` (insert statements: 1)
- `Contact Phone` (insert statements: 1)
- `Country` (insert statements: 1)
- `Currency` (insert statements: 1)
- `Currency Exchange` (insert statements: 1)
- `Currency Exchange Settings Details` (insert statements: 1)
- `Currency Exchange Settings Result` (insert statements: 1)
- `Custom DocPerm` (insert statements: 1)
- `Custom Field` (insert statements: 1)
- `Custom HTML Block` (insert statements: 1)
- `Custom Role` (insert statements: 1)
- `Dashboard` (insert statements: 1)
- `Dashboard Chart` (insert statements: 1)
- `Dashboard Chart Field` (insert statements: 1)
- `Dashboard Chart Link` (insert statements: 1)
- `Dashboard Chart Source` (insert statements: 1)
- `Dashboard Settings` (insert statements: 1)
- `Domain` (insert statements: 1)
- `Email Account` (insert statements: 1)
- `Email Domain` (insert statements: 1)
- `Email Flag Queue` (insert statements: 1)
- `Email Group` (insert statements: 1)
- `Email Group Member` (insert statements: 1)
- `Email Template` (insert statements: 1)
- `Email Unsubscribe` (insert statements: 1)
- `Energy Point Rule` (insert statements: 1)
- `Event management` (insert statements: 1)
- `Expected Skill Set` (insert statements: 1)
- `Expense Claim Account` (insert statements: 1)
- `Finance Book` (insert statements: 1)
- `Form Tour` (insert statements: 1)
- `Form Tour Step` (insert statements: 1)
- `Gender` (insert statements: 1)
- `Global Search DocType` (insert statements: 1)
- `Grocery Items` (insert statements: 1)
- `Grocery Shopping` (insert statements: 1)
- `HD Article` (insert statements: 1)
- `HD Article Category` (insert statements: 1)
- `HD Service Day` (insert statements: 1)
- `HD Service Holiday List` (insert statements: 1)
- `HD Service Level Agreement` (insert statements: 1)
- `HD Service Level Priority` (insert statements: 1)
- `HD Team` (insert statements: 1)
- `HD Team Member` (insert statements: 1)
- `HD View` (insert statements: 1)
- `Has Role` (insert statements: 1)
- `Help Category` (insert statements: 1)
- `IMAP Folder` (insert statements: 1)
- `IP Address` (insert statements: 1)
- `IP Pool` (insert statements: 1)
- `Incoterm` (insert statements: 1)
- `Industry Type` (insert statements: 1)
- `Insights Chart v3` (insert statements: 1)
- ... 152 more

### Projects (8)
- `Project Record` (insert statements: 1)
- `Project Template` (insert statements: 1)
- `Project Template Task` (insert statements: 1)
- `Project Type` (insert statements: 1)
- `Project User` (insert statements: 1)
- `Task Depends On` (insert statements: 1)
- `Task Management` (insert statements: 1)
- `Task Type` (insert statements: 1)

### Support (7)
- `HD Ticket Activity` (insert statements: 10)
- `HD Ticket Comment` (insert statements: 1)
- `HD Ticket Priority` (insert statements: 1)
- `HD Ticket Status` (insert statements: 1)
- `HD Ticket Type` (insert statements: 1)
- `Issue Priority` (insert statements: 1)
- `Issue Type` (insert statements: 1)

### System/Logs (20)
- `Version` (insert statements: 1200)
- `Notification Log` (insert statements: 225)
- `Scheduled Job Log` (insert statements: 196)
- `Access Log` (insert statements: 106)
- `Email Queue` (insert statements: 104)
- `Error Log` (insert statements: 98)
- `Data Import Log` (insert statements: 22)
- `Route History` (insert statements: 22)
- `Webhook Request Log` (insert statements: 22)
- `ToDo` (insert statements: 14)
- `PWA Notification` (insert statements: 8)
- `View Log` (insert statements: 8)
- `Email Queue Recipient` (insert statements: 3)
- `Activity Log` (insert statements: 2)
- `Changelog Feed` (insert statements: 1)
- `Console Log` (insert statements: 1)
- `Data Import` (insert statements: 1)
- `DefaultValue` (insert statements: 1)
- `Patch Log` (insert statements: 1)
- `Prepared Report` (insert statements: 1)

## Child-Table Gaps Around Already-Covered Parents
### Asset
- `Asset Activity` (insert statements: 1)
- `Asset Category` (insert statements: 1)
- `Asset Category Account` (insert statements: 1)
- `Asset Movement` (insert statements: 1)
- `Asset Movement Item` (insert statements: 1)

### Asset Category
- `Asset Category Account` (insert statements: 1)

### Attendance
- `Attendance Request` (insert statements: 1)

### Customer
- `Customer Group` (insert statements: 1)

### Department
- `Department Approver` (insert statements: 1)

### Designation
- `Designation Skill` (insert statements: 1)

### Employee
- `Employee Boarding Activity` (insert statements: 1)
- `Employee Checkin` (insert statements: 1)
- `Employee Education` (insert statements: 1)
- `Employee External Work History` (insert statements: 1)
- `Employee Feedback Criteria` (insert statements: 1)
- `Employee Feedback Rating` (insert statements: 1)
- `Employee Grade` (insert statements: 1)
- `Employee Onboarding Template` (insert statements: 1)

### Expense Claim
- `Expense Claim Account` (insert statements: 1)
- `Expense Claim Type` (insert statements: 1)

### HD Ticket
- `HD Ticket Activity` (insert statements: 10)
- `HD Ticket Comment` (insert statements: 1)
- `HD Ticket Priority` (insert statements: 1)
- `HD Ticket Status` (insert statements: 1)
- `HD Ticket Type` (insert statements: 1)

### Issue
- `Issue Priority` (insert statements: 1)
- `Issue Type` (insert statements: 1)

### Item
- `Item Attribute` (insert statements: 1)
- `Item Attribute Value` (insert statements: 1)
- `Item Default` (insert statements: 1)
- `Item Group` (insert statements: 1)
- `Item Maintenace` (insert statements: 1)
- `Item Price` (insert statements: 1)
- `Item Tax` (insert statements: 1)
- `Item Tax Template` (insert statements: 1)
- `Item Tax Template Detail` (insert statements: 1)

### Payment Entry
- `Payment Entry Deduction` (insert statements: 1)

### Project
- `Project Record` (insert statements: 1)
- `Project Template` (insert statements: 1)
- `Project Template Task` (insert statements: 1)
- `Project Type` (insert statements: 1)
- `Project User` (insert statements: 1)

### Purchase Invoice
- `Purchase Invoice Advance` (insert statements: 1)

### Sales Invoice
- `Sales Invoice Advance` (insert statements: 2)

### Supplier
- `Supplier Group` (insert statements: 1)
- `Supplier Scorecard Standing` (insert statements: 1)
- `Supplier Scorecard Variable` (insert statements: 1)

### Task
- `Task Depends On` (insert statements: 1)
- `Task Management` (insert statements: 1)
- `Task Type` (insert statements: 1)

### Warehouse
- `Warehouse Type` (insert statements: 1)
