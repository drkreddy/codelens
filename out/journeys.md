# API journeys

| Repo | Service | Files | Entry points | Outbound calls |
|---|---|---|---|---|
| card-adapter | card-adapter | 4 | 1 | 2 |
| mobile-banking-api | mobile-banking-api | 11 | 3 | 3 |
| payments-service | payments-service | 5 | 2 | 3 |

## Systems reached

| System | Category | Protocol · operation | Journeys | Called by |
|---|---|---|---|---|
| **DCMS** | Internal core | ISO 8583 MTI 0302 | 2 | `DcmsClient.blockCard` |
| **Finacle (CBS)** | Internal core | HTTP POST /gateway/FIN_CREDIT_TXN (via ESB)<br>HTTP POST /gateway/FIN_DEBIT_TXN (via ESB)<br>SOAP marshalSendAndReceive | 3 | `EsbCoreBankingAdapter.credit`, `EsbCoreBankingAdapter.debit`, `FinacleGateway.getBalance` |
| **FSS** | Third-party | HTTP POST /api/v1/hotlist | 2 | `FssHotlistClient.hotlist` |
| **NPCI (UPI)** | Third-party | HTTP POST /upi/ReqPay/2.0 | 1 | `NpciUpiClient.sendReqPay` |
| **UIDAI (Aadhaar)** | Third-party | HTTP POST /2.5/auth | 1 | `UidaiAuthClient.authenticate` |

## What the domain layer depends on

| Domain class | Internal core | Internal services | Third-party |
|---|---|---|---|
| `CardBlockService` (card-adapter) | DCMS | — | FSS |
| `AadhaarGateway` (mobile-banking-api) | — | — | UIDAI (Aadhaar) |
| `AccountService` (mobile-banking-api) | Finacle (CBS) | — | — |
| `CardService` (mobile-banking-api) | DCMS | card-adapter | FSS |
| `CoreBankingGateway` (mobile-banking-api) | Finacle (CBS) | — | — |
| `KycService` (mobile-banking-api) | — | — | UIDAI (Aadhaar) |
| `CoreBankingPort` (payments-service) | Finacle (CBS) | — | — |
| `UpiPaymentService` (payments-service) | Finacle (CBS) | — | NPCI (UPI) |

## Journeys

### DCMS

```
POST /api/cards/{cardId}/block   [card-adapter]
  card-adapter         web             CardBlockController.block
  card-adapter         domain          CardBlockService.block
  card-adapter         infrastructure  DcmsClient.blockCard
  → DCMS · ISO 8583 MTI 0302
    tcp://dcms-switch.bank.internal:7002
```
Confidence: likely · matched by host

```
POST /cards/{id}/block   [mobile-banking-api]
  mobile-banking-api   web             CardController.block
  mobile-banking-api   domain          CardService.block
  mobile-banking-api   infrastructure  CardAdapterClient.blockCard
                                       ── HTTP POST /api/cards/{cardId}/block ──▶
  card-adapter         web             CardBlockController.block
  card-adapter         domain          CardBlockService.block
  card-adapter         infrastructure  DcmsClient.blockCard
  → DCMS · ISO 8583 MTI 0302
    tcp://dcms-switch.bank.internal:7002
```
Confidence: likely · matched by host

### FSS

```
POST /api/cards/{cardId}/block   [card-adapter]
  card-adapter         web             CardBlockController.block
  card-adapter         domain          CardBlockService.block
  card-adapter         infrastructure  FssHotlistClient.hotlist
  → FSS · HTTP POST /api/v1/hotlist
    https://hotlist.fss.example/api/v1/hotlist
```
Confidence: likely · matched by host

```
POST /cards/{id}/block   [mobile-banking-api]
  mobile-banking-api   web             CardController.block
  mobile-banking-api   domain          CardService.block
  mobile-banking-api   infrastructure  CardAdapterClient.blockCard
                                       ── HTTP POST /api/cards/{cardId}/block ──▶
  card-adapter         web             CardBlockController.block
  card-adapter         domain          CardBlockService.block
  card-adapter         infrastructure  FssHotlistClient.hotlist
  → FSS · HTTP POST /api/v1/hotlist
    https://hotlist.fss.example/api/v1/hotlist
```
Confidence: likely · matched by host

### Finacle (CBS)

```
GET /accounts/{id}/balance   [mobile-banking-api]
  mobile-banking-api   web             AccountController.balance
  mobile-banking-api   domain          AccountService.balance
  mobile-banking-api   domain          CoreBankingGateway.getBalance
  mobile-banking-api   infrastructure  ⇒ FinacleGateway.getBalance
  → Finacle (CBS) · SOAP marshalSendAndReceive
    https://finacle-uat.bank.internal/FIWebService/balance
```
Confidence: likely · matched by host

```
KAFKA upi.callback   [payments-service]
  payments-service     domain          UpiPaymentService.onCallback
  payments-service     domain          CoreBankingPort.credit
  payments-service     infrastructure  ⇒ EsbCoreBankingAdapter.credit
  → Finacle (CBS) via ESB · HTTP POST /gateway/FIN_CREDIT_TXN
    http://esb.bank.internal/gateway/FIN_CREDIT_TXN
```
Confidence: likely · matched by marker

```
POST /upi/pay   [payments-service]
  payments-service     web             UpiController.pay
  payments-service     domain          UpiPaymentService.pay
  payments-service     domain          CoreBankingPort.debit
  payments-service     infrastructure  ⇒ EsbCoreBankingAdapter.debit
  → Finacle (CBS) via ESB · HTTP POST /gateway/FIN_DEBIT_TXN
    http://esb.bank.internal/gateway/FIN_DEBIT_TXN
```
Confidence: likely · matched by marker

### NPCI (UPI)

```
POST /upi/pay   [payments-service]
  payments-service     web             UpiController.pay
  payments-service     domain          UpiPaymentService.pay
  payments-service     infrastructure  NpciUpiClient.sendReqPay
  → NPCI (UPI) · HTTP POST /upi/ReqPay/2.0
    https://upi-gw.npci.example/upi/ReqPay/2.0
```
Confidence: likely · matched by host

### UIDAI (Aadhaar)

```
POST /kyc/aadhaar   [mobile-banking-api]
  mobile-banking-api   web             KycController.verify
  mobile-banking-api   domain          KycService.verify
  mobile-banking-api   domain          AadhaarGateway.authenticate
  mobile-banking-api   infrastructure  ⇒ UidaiAuthClient.authenticate
  → UIDAI (Aadhaar) · HTTP POST /2.5/auth
    https://auth.uidai.example/2.5/auth
```
Confidence: likely · matched by host

Config values that look like secrets are masked when read and never appear in this report.
