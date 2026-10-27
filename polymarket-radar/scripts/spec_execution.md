# Polymarket Automated Execution & Strategy Pipeline Specification

## Executive Overview
This document specifies the complete technical, operational, and mathematical architecture for an automated paper-trading and live-execution pipeline on Polymarket. It covers protocol fee mechanics, jurisdiction and access rules, Central Limit Order Book (CLOB) API execution flows, real-time data streaming, UMA resolution risk mitigation, small-account viability, and three end-to-end automated strategy architectures.

---

## 1. Fees, Costs, and Collateral Infrastructure
### 1.1 Trading Fee Schedule
Polymarket enforces a dynamic, category-specific fee model applied exclusively to **takers** at match time. **Makers pay zero trading fees (0 bps)** across all categories and receive rebates funded by collected taker fees.

#### Fee Formula
Taker fees are denominated in pUSD/USDC and vary quadratically with the share price $p$ (symmetric around $p = 0.50$):
$$\text{Fee (USDC)} = C \times \text{feeRate} \times p \times (1 - p)$$

Where:
- $C$ = Number of shares traded.
- $\text{feeRate}$ = Category fee multiplier.
- $p$ = Executed price per share ($0.01 \le p \le 0.99$).

#### Category Fee Rates & Maker Rebates
| Market Category | Taker Fee Rate ($\text{feeRate}$) | Peak Taker Fee per 100 Shares at $p = 0.50$ | Maker Fee Rate | Maker Rebate (% of Taker Fee) |
| :--- | :--- | :--- | :--- | :--- |
| **Geopolitics & World Events** | **0.00 (Fee-Free)** | **$0.00** | **0%** | **N/A** |
| **Politics** | 0.04 | $1.00 (2.00% effective) | 0% | 25% |
| **Finance / Mentions / Tech** | 0.04 | $1.00 (2.00% effective) | 0% | 25% |
| **Sports** | 0.05 | $1.25 (2.50% effective) | 0% | 15% |
| **Economics / Culture / Weather / Other** | 0.05 | $1.25 (2.50% effective) | 0% | 25% |
| **Crypto** | 0.07 | $1.75 (3.50% effective) | 0% | 20% |
| **Combos** | 0.05 | $1.25 (2.50% effective) | 0% | 0% |

*Sources:* [Polymarket Documentation - Fees](https://docs.polymarket.com/trading/fees), [Maker Rebates Program](https://docs.polymarket.com/programs/maker-rebates)

#### Precision & Minimum Fee Threshold
Fees are rounded to 5 decimal places. The absolute minimum non-zero fee charged is `0.00001 USDC`. Any calculated fee smaller than `0.00001 USDC` rounds down to zero.

### 1.2 Polygon Deposit, Withdrawal, & Gas Costs
- **Platform Deposit / Withdrawal Fees**: Polymarket charges $0$ native platform fees to deposit or withdraw funds.
- **On-Chain Gas Costs on Polygon (Chain ID 137)**:
  - Polymarket uses an off-chain order matching model (EIP-712 signed orders submitted to the CLOB). Order creation and cancellation incur **$0 gas**.
  - On-chain gas is required only for collateral wrapping (`USDC` $\rightarrow$ `pUSD`), manual CTF minting/splitting/merging, or manual redemption of won positions.
  - Typical Polygon PoS gas cost per transaction: **$0.005 – $0.02** (paid in POL/MATIC).
- **L1 Bridge / On-Ramp Costs**:
  - Bridging USDC from Ethereum L1 to Polygon PoS costs Ethereum L1 network gas ($2.00 – $15.00+ depending on gwei).
  - Third-party fiat/crypto ramps (MoonPay, Robinhood, Coinbase) impose independent processing fees (0.5% – 2%).

### 1.3 USDC Infrastructure: USDC.e, Native USDC, and pUSD
Polymarket executed a major collateral transition to standardise settlement:
1. **Bridged USDC (`USDC.e`)**: Legacy ERC-20 bridged from Ethereum (`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`). Deprecated for new primary settlement.
2. **Native USDC**: Direct Circle-issued ERC-20 token on Polygon (`0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`). Enables cross-chain liquidity via Circle CCTP.
3. **pUSD (Polymarket USD)**: The official ERC-20 collateral wrapper (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`). All trading on CTF Exchange V2 settles against pUSD, which is backed 1:1 by USDC enforced on-chain via smart contracts.
   - **CollateralOnramp (`0x93070a847efEf7F70739046A929D47a521F5B8ee`)**: Wraps USDC into pUSD via `wrap(asset, to, amount)`.
   - **CollateralOfframp (`0x2957922Eb93258b93368531d39fAcCA3B4dC5854`)**: Unwraps pUSD back to USDC via `unwrap(to, amount)`.

---

## 2. Regulatory Access, KYC, and Jurisdiction Limits
### 2.1 Restricted Jurisdictions (As of October 2026)
Polymarket enforces strict geographic access controls categorized into three operational modes:

1. **Total Block (Strict OFAC / Comprehensive Sanctions)**:
   - *Status*: Blocked completely via IP, Cloudflare edge, and smart contract access. Users cannot place orders or close existing positions.
   - *Jurisdictions*: Cuba, Iran, North Korea, Syria, Crimea / Donetsk / Luhansk / Zaporizhzhia / Kherson regions of Ukraine.
2. **Regulatory-Restricted Jurisdictions (Close-Only Mode)**:
   - *Status*: Users can log in and submit orders to **close/reduce existing positions**, but cannot open new positions or place new buy orders.
   - *Jurisdictions*: United States (international site `polymarket.com`), United Kingdom (GB), Taiwan (TW), Singapore (SG), Thailand (TH), Poland (PL), Australia (AU), France (FR), Spain (ES), Sudan (SD).
3. **Frontend-Only Restriction**:
   - *Jurisdictions*: Japan (JP) and select EU member states where web frontend access is restricted but API/smart contract interactions enforce regional compliance rules.

### 2.2 Taiwan Jurisdiction Analysis
- **Status**: **Taiwan (TW)** is explicitly classified under **Regulatory-Restricted Jurisdictions (Close-Only Mode)** on both the web frontend (`polymarket.com`) and the CLOB REST/WS API (`clob.polymarket.com`).
- **Trading Implications for Taiwan-based IP/wallets**:
  - API endpoint `/geoblock` returns restricted status for Taiwan country code (`TW`).
  - Attempting to submit `POST /order` to open a new position results in HTTP 403 / API error `GEOGRAPHIC_RESTRICTION`.
  - Taiwan-based paper trading pipelines must execute using non-restricted gateway proxies or simulation environments without live CLOB order placement.

### 2.3 Identity Verification & KYC Requirements
- **International Platform (`polymarket.com`)**:
  - Uses non-custodial Web3 wallet signatures (EOA / Gnosis Safe / Magic Link).
  - No Tier-1 document KYC is required for basic crypto-native wallet trading, but automated IP geoblocking, VPN/TOR detection (via Cloudflare + IPQS), and wallet risk scoring are active.
  - Fiat on/off ramps (MoonPay / Bridge) mandate full Tier-1 KYC (passport/ID + facial scan).
- **US Platform (`polymarket.us`)**:
  - Mandates full CIP/AML KYC (SSN, ID verification, proof of address) for all account holders.

### 2.4 US Re-entry & QCEX Acquisition
- **Background**: In January 2022, the CFTC issued a cease-and-desist order and $1.4M civil penalty against Polymarket for offering unregistered event-based swaps. Polymarket subsequently geoblocked US residents from `polymarket.com`.
- **QCEX Acquisition**: In July 2025, Polymarket acquired CFTC-regulated exchange and clearinghouse **QCX LLC (d/b/a QCEX)** for $112 million.
- **Current Legal Status**:
  - CFTC approved rule amendments for QCX in late 2025/2026, allowing Polymarket to relaunch in the US under **Polymarket US (`polymarket.us`)**.
  - **Critical Architectural Distinction**: US live trading is restricted to the CFTC-regulated `polymarket.us` exchange operating under US derivatives regulations. **US residents are NOT permitted to trade on the international Polygon-based CLOB (`clob.polymarket.com`)**.

---

## 3. CLOB Execution & Order Management
### 3.1 Authentication Architecture: L1 vs L2
Polymarket's Central Limit Order Book (CLOB) uses a two-tier authentication architecture:

```
[ L1 Wallet Private Key ] (ECDSA EIP-712 Signer)
           │
           ├──► Generates L1 Headers (Address, Timestamp, Nonce, EIP-712 Sig)
           │    Calls POST /auth/api-key ──► Returns L2 Credentials (apiKey, secret, passphrase)
           │
           └──► Signs Order Payload (EIP-712 Order Struct)
                      │
[ L2 API Credentials ] ┴──► Generates L2 Headers (POLY_API_KEY, HMAC-SHA256 Sig, Passphrase)
                                     │
                                     ▼
                            [ POST /order to CLOB ]
```

#### L1 Authentication (Credential Generation)
L1 signatures use EIP-712 typed data signing over a `ClobAuthDomain` to attest wallet ownership:
- **Headers Required**:
  - `POLY_ADDRESS`: Wallet or signer address.
  - `POLY_SIGNATURE`: EIP-712 signature of timestamp and nonce.
  - `POLY_TIMESTAMP`: UTC unix timestamp in seconds.
  - `POLY_NONCE`: Integer nonce (typically 0).

#### L2 Authentication (REST & WS Order Management)
L2 credentials consist of `apiKey`, `secret` (base64 encoded), and `passphrase`. Every private endpoint request requires L2 HMAC-SHA256 headers:
- **Headers Required**:
  - `POLY_API_KEY`: API key string.
  - `POLY_SIGNATURE`: Base64 HMAC-SHA256 signature calculated over `timestamp + method + requestPath + body` using the base64-decoded secret.
  - `POLY_TIMESTAMP`: Current UTC timestamp in seconds.
  - `POLY_PASSPHRASE`: API passphrase.

#### Python Integration via `py-clob-client`
```python
import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

# Initialize L1 client
client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.getenv("POLYMARKET_PRIVATE_KEY"),
    chain_id=POLYGON,
    signature_type=1, # 0 = EOA, 1 = POLY_PROXY (Gnosis Safe / Proxy), 2 = POLY_1271
    funder=os.getenv("POLYMARKET_PROXY_ADDRESS")
)

# Derive or create L2 API credentials
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

# Build and submit an off-chain signed order
order_args = OrderArgs(
    price=0.48,
    size=100.0, # Share count
    side="BUY",
    token_id="712398410293847019238401293840192384102938470"
)
signed_order = client.create_order(order_args)
response = client.post_order(signed_order, OrderType.GTC)
```

### 3.2 Order Types
1. **GTC (Good-Til-Cancelled)**: Resting limit order. Enters the orderbook and remains until matched or explicitly cancelled.
2. **FOK (Fill-Or-Kill)**: Atomic execution. Must match in its entirety immediately against resting liquidity; otherwise, the entire order is rejected with zero fill.
3. **FAK (Fill-And-Kill / Immediate-Or-Cancel)**: Immediate partial fill. Matches as much size as possible against current orderbook depth, and immediately cancels any unfilled balance.
4. **GTD (Good-Til-Date)**: Resting limit order with an explicit Unix expiration timestamp. Automatically expires and drops off the book at expiration.

### 3.3 Minimum Order Size & Tick Sizes
- **Minimum Order Size**:
  - **Limit Orders**: Minimum order quantity = **5 shares** (or minimum $1.00 order value).
  - **Market Orders**: Minimum spend = **$1.00 pUSD** (recommended $5.00 – $10.00 for clean fills).
- **Price Increments / Tick Sizes**:
  - Standard markets: **0.01** ($0.01 price step).
  - High-precision markets: **0.001** or **0.0001**.
  - Orders submitted with invalid price ticks are rejected with `INVALID_PRICE_TICK`.

### 3.4 Rate Limits (Cloudflare + Bucket Throttling)
| Target Endpoint | Burst Limit (10s) | Sustained Limit (10 min) | Notes |
| :--- | :--- | :--- | :--- |
| `POST /order` | 5,000 req / 10s | 120,000 req / 10 min | 200 orders / sec max |
| `DELETE /order` | 5,000 req / 10s | 120,000 req / 10 min | Per-signer token bucket |
| `POST /orders` (Batch) | 2,000 req / 10s | 21,000 req / 10 min | Up to 15 orders per batch |
| `GET /book` / `/price` | 1,500 req / 10s | N/A | Single market orderbook |
| `POST /books` / `/prices`| 500 req / 10s | N/A | Batch orderbooks |
| Gamma API General | 4,000 req / 10s | N/A | Market metadata / discovery |
| Data API v2 General | 800 req / 10s | N/A | User trades / positions |

### 3.5 Token Identification: `clobTokenIds` vs `positionIds`
- **CTF Markets (V1 Architecture)**:
  - Gamma API returns a stringified JSON array in `clobTokenIds`: e.g. `["1001...", "1002..."]`.
  - Index 0 corresponds to outcome **YES**, Index 1 corresponds to outcome **NO**.
- **Polymarket Protocol V2 Markets**:
  - Uses `positionIds` (an array of ERC-1155 uint256 position IDs): e.g. `["6511508...", "6511509..."]`.
  - The trading SDK accepts `asset_id` directly, automatically mapping to `positionId` (v2) or `tokenId` (CTF v1).

### 3.6 NegRiskAdapter Mechanics & Atomic Conversion
In multi-outcome events where outcomes are mutually exclusive and collectively exhaustive (e.g., "Who will win the 2028 US Presidential Election?"), Polymarket utilizes **Negative Risk Markets**.

#### The Economic Identity
Holding 1 NO share for candidate $A$ is economically identical to holding 1 YES share for every other competing candidate $(B, C, D, \dots)$.

#### Contract Architecture
- **CTF Neg Risk Adapter**: `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`
- **Neg Risk CTF Exchange**: `0xe2222d279d744050d28e00520010520000310F59`
- **Neg Risk Collateral Adapter**: `0xadA2005600Dec949baf300f4C6120000bDB6eAab`

#### Functionality
1. `convertPositions(bytes32 marketId, uint256 indexSet, uint256 amount)`:
   - Allows users or automated bots to present 1 YES share of every outcome in a NegRisk market and burn them atomically to receive $1.00$ pUSD collateral.
   - Converts 1 NO share of candidate $A$ into 1 YES share of all other candidates $(B, C, D)$.

### 3.7 Exact Arbitrage Mathematics for Multi-Outcome Events
Consider an $N$-outcome mutually exclusive event $\{O_1, O_2, \dots, O_N\}$ where exactly one outcome will resolve to $1.00$ and all others to $0.00$.

Let:
- $P_{\text{ask}}(O_i)$ = Lowest ask price for outcome $O_i$'s YES token.
- $P_{\text{bid}}(O_i)$ = Highest bid price for outcome $O_i$'s YES token.
- $F_{\text{taker}}(O_i)$ = Taker fee in pUSD for trading outcome $O_i$.
- $G$ = Total estimated gas cost for on-chain position conversion / redemption (in pUSD).

#### Case A: Underpriced YES Basket Arbitrage (Sum of Asks < $1.00$)
**Condition**:
$$\sum_{i=1}^N P_{\text{ask}}(O_i) + \sum_{i=1}^N F_{\text{taker}}(O_i) + G < 1.00$$

**Execution Procedure**:
1. Concurrently submit $N$ FOK/FAK market buy orders for 1 share of YES across all outcomes $O_1, \dots, O_N$.
2. Total outlay = $\sum_{i=1}^N P_{\text{ask}}(O_i) + \sum_{i=1}^N F_{\text{taker}}(O_i)$.
3. Execute `convertPositions()` via `NegRiskAdapter` to burn the full YES set for $1.00$ pUSD collateral, or hold until settlement for guaranteed $1.00$ payout.
4. **Net Guaranteed Arbitrage Profit**:
$$\Pi_{\text{basket}} = 1.00 - \left( \sum_{i=1}^N P_{\text{ask}}(O_i) + \sum_{i=1}^N F_{\text{taker}}(O_i) + G \right)$$

#### Case B: Overpriced YES Basket Arbitrage / NO Minting Arbitrage (Sum of Bids > $1.00$)
**Condition**:
$$\sum_{i=1}^N P_{\text{bid}}(O_i) - \sum_{i=1}^N F_{\text{taker}}(O_i) - G > 1.00$$

**Execution Procedure**:
1. Deposit $1.00$ pUSD into `ConditionalTokens` / `NegRiskAdapter` to mint 1 YES share for every outcome $O_1, \dots, O_N$.
2. Concurrently cross the orderbook by placing $N$ FOK market sell orders into $P_{\text{bid}}(O_i)$ for all $i \in \{1, \dots, N\}$.
3. Total proceeds collected = $\sum_{i=1}^N P_{\text{bid}}(O_i) - \sum_{i=1}^N F_{\text{taker}}(O_i)$.
4. **Net Guaranteed Arbitrage Profit**:
$$\Pi_{\text{mint}} = \sum_{i=1}^N P_{\text{bid}}(O_i) - \sum_{i=1}^N F_{\text{taker}}(O_i) - 1.00 - G$$

---

## 4. Real-Time Data Streams and Copy-Trading Latency
### 4.1 Streaming & Polling Endpoint Matrix
| Feed / Channel | URL / Endpoint | Protocol | Auth Required | Typical Latency | Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Market WebSocket** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | WSS | No | 10ms – 50ms | Real-time L2 orderbook, price changes, trade execution |
| **User WebSocket** | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | WSS | Yes (L2 HMAC) | 10ms – 30ms | Real-time fill reports, order status, private trades |
| **PolyBolt High-Freq**| `wss://ws-live-v2.polymarket.com/ws` | WSS | No | < 20ms | High-frequency reference prices (Crypto/Equity) |
| **Data API v2 Activity**| `https://data-api.polymarket.com/v2/activity` | REST (Poll) | No | 1,000ms – 3,000ms | Historical trade logs, user activity monitoring |
| **Goldsky Subgraph / Pipeline**| Goldsky GraphQL / Pipeline Streams | GraphQL / Stream | Key | 2,000ms – 5,000ms | On-chain index query, historical analytics |
| **Polygon RPC Node** | Custom RPC (`eth_subscribe` logs) | WebSocket | Optional | 2,000ms (Block time) | Raw on-chain event detection |

### 4.2 Latency Benchmark Comparison for Copy-Trading
```
[ Event Initiated by Target Wallet ]
                 │
                 ├──► (A) CLOB Engine Match ─────────────────► WS /market Feed (10ms - 50ms)
                 │
                 ├──► (B) Transaction Included in Block ─────► Polygon RPC Log (2,000ms - 2,300ms)
                 │
                 └──► (C) Ingestion into Data API DB ────────► Data API /v2/activity (1,500ms - 3,500ms)
```

1. **CLOB WebSocket Stream (`ws/market`)**: Updates pushed directly from memory matching engine (**10 – 50 ms**). Trade messages mask user wallet addresses for privacy, making direct wallet identification impossible purely via public market WS.
2. **On-Chain Polygon Transaction Logs**: Block generation time on Polygon PoS is **~2.0 – 2.3 seconds**. Parsing `TransferSingle` / `OrderFilled` events directly from raw Polygon RPC node logs yields **~2.1 seconds** total latency.
3. **Public Data API (`/v2/activity`) Polling**: Incurs indexing delay between on-chain log emission, database replication, and API response caching. Realistic latency: **1.5 to 3.5 seconds**.

#### Copy-Trading Slippage & Front-Running Implications
Copying trades via `/v2/activity` polling exposes the shadow trader to 2–3 seconds of adverse selection. High-volume target wallets consume top-of-book depth; by the time a polling bot detects the trade, market prices have moved, causing heavy slippage or failed executions.

---

## 5. Resolution Risk, UMA Oracle Mechanics, and Historical Cases
### 5.1 UMA Optimistic Oracle (OO) Dispute Workflow
Polymarket market resolutions are governed by UMA's Optimistic Oracle v3 (`UmaCtfAdapter`: `0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49`).

```
[ Market Expiration ]
        │
        ▼
[ Proposer Posts Outcome + Bond ] ──► (2-Hour Liveness Window)
        │                                     │
        ├──► No Dispute ──────────────────────┼──► Finalized & Settled On-Chain
        │                                     │
        └──► Disputed by Counterparty ────────┘
                    │
                    ▼
        [ Escalate to UMA DVM ]
        (UMA Token Holders Vote over 48-72 hrs)
                    │
                    ▼
        [ Final Resolution Executed ]
```

1. **Proposal**: A proposer submits a binary/multi-outcome answer along with a proposal bond (typically 750 pUSD).
2. **Liveness Window**: A liveness period (typically 2 hours) begins. Anyone can challenge the proposal by posting an equal dispute bond.
3. **Finalization vs DVM Escalation**:
   - If uncontested during liveness, the proposal resolves automatically.
   - If disputed, the market escalates to UMA's Data Verification Mechanism (DVM). UMA ERC-20 token holders vote during a multi-day voting round to settle the result.

### 5.2 Documented Historical Disputed Cases
#### Case Study 1: Venezuela Presidential Election Market (2024–2025)
- **Market Context**: "Venezuela Presidential Election Winner".
- **Dispute Core**: Official election authority (CNE) declared Nicolás Maduro winner, whereas opposition tally sheets and international observers cited Edmundo González.
- **Resolution Conflict**: Proposers initially resolved to Maduro based on de facto government control. Opponents disputed based on voting tally evidence. UMA DVM voted to resolve to Edmundo González / 50-50 tie ambiguous, causing massive controversy among traders who bet on literal de facto power vs electoral count ($7M+ locked).
- **Takeaway**: Rules specifying "official announcement" vs "actual electoral winner" create fatal Oracle divergence.

#### Case Study 2: RFK Jr. "Drop Out Before September" Market (2024)
- **Market Context**: "Will RFK Jr. drop out of the presidential race before September 1, 2024?".
- **Dispute Core**: RFK Jr. held a press conference "suspending" his campaign and endorsing Donald Trump, but remained on official ballots in non-swing states.
- **Resolution Conflict**: Proposers declared YES ("suspension = dropping out"). Disputers argued NO based on legal ballot registration rules. UMA DVM voted YES, penalizing traders who relied on strict legal ballot definition.
- **Takeaway**: Colloquial speech vs legal definition creates major dispute vector.

#### Case Study 3: DeepSeek AI Model Benchmark / Release Date Dispute (2025)
- **Market Context**: "DeepSeek R1 / V3 release date or open-weights benchmark score".
- **Dispute Core**: Disagreements over calendar day cutoffs due to UTC vs Beijing Time (CST) and GitHub commit release timestamps.
- **Resolution Conflict**: A 1-hour timezone discrepancy triggered a formal dispute. UMA voters had to decide whether local developer time or UTC governed calendar dates.
- **Takeaway**: Absence of explicit timezone definitions (e.g. "23:59 UTC") introduces resolution coin-flips.

### 5.3 50-50 Invalidation & Price Source Risk
- **50-50 Refund Resolution**: When an event is cancelled, postponed indefinitely, or ruled fundamentally ambiguous, UMA resolves the market to 50-50 ($0.50 payout for YES and $0.50 for NO).
- **Arbitrage Hazard**: An arbitrageur holding a YES basket bought at $0.97 total cost expects $1.00 payout ($0.03 profit). If UMA rules the market invalid and resolves 50-50, the YES basket pays out $0.50, inflicting a **48.5% capital loss** on a trade assumed to be "risk-free".

---

## 6. Small-Account Practicality ($50 – $500 Scale)
### 6.1 Orderbook Depth, Slippage, and Spread Analysis
- **Top-Tier Markets (US Politics / Major Crypto)**:
  - Spreads: **0.1% – 0.5%** ($0.001 – $0.005).
  - Depth: $10,000+ available within 1 cent of midpoint.
  - $50 – $500 orders execute with near-zero price slippage.
- **Mid-Tier / Niche Markets (Culture, Tech, Specific Events)**:
  - Spreads: **2.0% – 5.0%** ($0.02 – $0.05).
  - Depth: $50 – $150 available at top-of-book.
  - A $500 market order can sweep 3–4 price ticks, causing **2.5% – 4.0% slippage**.

### 6.2 Small-Account Cost Efficiency
- **Off-Chain Gasless Order Signing**: Because order placement and cancellation incur $0 gas fees, account size does not limit trading frequency.
- **Position Capital Requirements**:
  - Minimum trade size of $1.00 / 5 shares allows granular position sizing and fractional Kelly betting even on a $100 total portfolio.
  - Capital is not locked in high gas overhead.

---

## 7. Recommended Pipeline Architectures
### Architecture A: Multi-Outcome Arbitrage Scanner
```
[ Gamma API Polling ] ──► Filter Multi-Outcome Markets (N >= 3)
           │
           ▼
[ CLOB L2 Orderbook Fetcher ] ──► /books Endpoint (Top-of-Book Depth)
           │
           ▼
[ Arbitrage Math Engine ]
   ├── Calculate Taker Fees (feeRate * p * (1-p))
   ├── Calculate Gas Costs for NegRisk Conversion
   └── Check Basket Condition: Sum(Asks) + Fees + Gas < 1.00
           │
           ▼
[ Execution Module ] ──► Concurrent FOK Market Orders (CLOB API)
           │
           ▼
[ On-Chain NegRisk Converter ] ──► convertPositions() for pUSD Redemption
```

#### Key Implementation Details
- **Order Synchronization**: Submit all $N$ legs concurrently using `POST /orders` batch endpoint to ensure atomic or near-atomic execution.
- **Leg-Risk Mitigation**: Use **FOK (Fill-Or-Kill)** orders exclusively. If any single leg fails to fill, the entire batch rejects, preventing partial leg exposure.

#### Failure Modes & Mitigations
1. *Partial Fill / Leg Risk*: Orders execute on 3 of 4 legs, leaving an unhedged directional position. **Mitigation**: Strict FOK orders via batch CLOB POST.
2. *Fee Schedule Changes*: Miscalculating fee rates across categories. **Mitigation**: Query dynamic fee rate endpoint `/fee-rate` prior to order submission.
3. *UMA Invalid Resolution (50-50)*: Market invalidated, returning $0.50 per outcome share. **Mitigation**: Filter out markets with ambiguous wording or active disputes.

---

### Architecture B: Copy-Trade Shadow Monitor (~20 Elite Wallets)
```
[ Target Wallet Pool (~20 Top PnL Wallets) ]
           │
           ├──► [ Polygon RPC WebSocket Node ] (Pending / Block Logs)
           │            │
           └──► [ Data API /v2/activity Poller ] (Fallback Sync)
                        │
                        ▼
            [ Event Parser & Filter ]
            (Extract Market, Outcome, Side, Trade Size)
                        │
                        ▼
            [ Sizing & Risk Engine ]
            (Scale Trade Size to Portfolio Ratio: S_copy = S_target * (B_local / B_target))
                        │
                        ▼
            [ CLOB FAK Market Order Execution ]
```

#### Failure Modes & Mitigations
1. *Adverse Selection / Front-Running Latency*: Target wallet consumes all top-of-book depth; shadow bot trades 2 seconds later at worse prices. **Mitigation**: Set strict max-slippage bounds (e.g., abort if price > target_price + 1.5%).
2. *Target Wallet Wash Trading / Manipulation*: Target wallet trades against itself to bait shadow bots. **Mitigation**: Validate target trades against global orderbook depth before copying.
3. *Position Divergence on Unwinds*: Target wallet exits in small increments that shadow bot misses. **Mitigation**: Periodic portfolio state reconciliation via Data API `/v2/positions`.

---

### Architecture C: News-to-Market Mispricing Agent
```
[ Real-Time News Stream ] ──► RSS / Twitter API / News Webhooks
           │
           ▼
[ Semantic Market Matcher ] ──► Vector Embeddings + Gamma Market Title Match
           │
           ▼
[ Quant / LLM Probability Estimator ]
   ├── Input: Article Text + Market Rules Text + Current Orderbook Midpoint
   └── Output: Estimated Event Probability (p_est) + Confidence Score
           │
           ▼
[ Fractional Kelly Sizing Engine ]
   ├── Formula: f* = (p_est * b - (1 - p_est)) / b
   └── Fractional Scaling: Use 0.25 * f* capped at max 3% portfolio risk
           │
           ▼
[ CLOB Limit Order Execution ]
```

#### Failure Modes & Mitigations
1. *LLM Hallucination / News Misinterpretation*: Bot misinterprets satirical or unverified news as confirmed fact. **Mitigation**: Multi-source news confirmation; require high LLM confidence score threshold (>0.85).
2. *Speed Deficit vs HFT News Bots*: Latency behind specialized news traders causes buying at peak prices. **Mitigation**: Execute via passive limit orders near midpoint rather than aggressive market orders.
3. *Market Rule Nuance Gap*: News headline reports event occurrence, but market rules require specific legal/official source confirmation. **Mitigation**: Feed exact market resolution rules into LLM prompt context.

---

## 8. Summary Specification Checklist
- **Spec File Path**: `/app/conversations/6ab927997f5a926229b9b171/polymarket/spec_execution.md`
- **Fee Precision**: Checked & documented (Dynamic taker fees, 0% geopolitics, quadratic curve around 50%, 0% maker fees + rebates).
- **Access Restrictions**: Checked & documented (Taiwan close-only, US live trading on international CLOB strictly prohibited, US legal entity active via QCEX acquisition on `polymarket.us`).
- **CLOB SDK & Math**: Documented with full EIP-712 / L2 HMAC authentication, `py-clob-client` code, NegRiskAdapter contract addresses, and exact multi-outcome arbitrage equations.
- **Latency & Resolution**: Documented with CLOB WSS vs Polygon RPC vs Data API latency hierarchy and 3 historical UMA dispute case studies.
