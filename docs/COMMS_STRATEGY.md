# AlgoChains Waitlist Onboarding — Communications Strategy

## Overview

AlgoChains operates as a **two-sided marketplace**:
- **Creators (Developers):** Build, validate, and publish trading strategies
- **Consumers (Subscribers):** Discover, verify, and deploy validated bots

Both sides have waitlists at **algochains.ai** and **algochains.io**. This document outlines the communications strategy for onboarding both audiences.

---

## Part 1: Developer Onboarding (Creator Side)

### Target Audience
- Quantitative developers, algo traders, data scientists
- People who build trading strategies in Python/Rust/C++
- AI/ML engineers interested in financial markets
- Registered at algochains.ai/io waitlist with developer interest

### Onboarding Funnel

```
Waitlist Signup → Welcome Email (Day 0)
    → Getting Started Guide (Day 1)
    → MCP Server Setup Tutorial (Day 3)
    → First Strategy Submission (Day 7)
    → MCPT Validation Walkthrough (Day 10)
    → Marketplace Listing (Day 14)
    → First Revenue (Day 30+)
```

### Email Sequence

#### Email 1: Welcome (Day 0 — Immediate)
**Subject:** You're in. Here's how to list your first bot on AlgoChains.

```
Hey {first_name},

Welcome to the AlgoChains developer program. You now have access to:

1. The AlgoChains MCP Server — connect any AI agent to any broker
2. The MCPT Validation Pipeline — institutional-grade strategy testing
3. The Marketplace — publish bots and earn recurring revenue

Your developer credentials:
- Dashboard: https://algochains.ai/dashboard
- API Docs: https://algochains.ai/docs
- MCP Server: pip install algochains-mcp-server

Quick start (5 minutes):
  pip install algochains-mcp-server
  cp .env.example .env
  # Add your LISTING_API_KEY from your dashboard
  algochains-mcp

Questions? Reply to this email or join our Discord.

— Tyler, Founder @ AlgoChains
```

#### Email 2: Getting Started Tutorial (Day 1)
**Subject:** Your first strategy submission in 15 minutes

```
Hey {first_name},

Yesterday you got access. Today, let's submit your first strategy.

Here's the fastest path:

1. Install the MCP server (if you haven't):
   pip install "algochains-mcp-server[all]"

2. Open your IDE (Windsurf, Cursor, or Claude Desktop)
   Add algochains to your MCP config (see README)

3. Ask your AI agent:
   "Submit my RSI strategy for MCPT validation"
   
   The agent will walk you through the required fields:
   - Symbol, strategy type, timeframe
   - OOS Sharpe, trade count, max drawdown
   - MCPT permutation test results
   - Walk-forward fold data

4. The 6-gate validation runs automatically:
   Schema → Performance → Overfitting → MCPT → Walk-Forward → Paper

Your strategy gets tiered: Platinum, Gold, Silver, or Bronze.
Platinum bots earn the most subscriber revenue.

Full guide: https://algochains.ai/docs/developer-guide

— Tyler
```

#### Email 3: Advanced Features (Day 3)
**Subject:** Metrics verification — how subscribers trust your bot

```
Hey {first_name},

The #1 question subscribers ask: "How do I know these metrics are real?"

Here's how AlgoChains verifies every bot:

1. Rust Backtest Engine — we re-run your strategy on historical data
2. Walk-Forward Validation — 4+ time periods, no look-ahead bias
3. MCPT Test — 1000 permutations, p < 0.05 significance
4. Paper Trading — 30 days of live market paper fills
5. Live Metrics — SHA-256 hashed trade logs, broker fill IDs

This verification chain is what makes AlgoChains different from
every other bot marketplace. Subscribers can audit every claim.

Your next step: Push live metrics from your running bot.
  
  Set METRICS_INGEST_API_KEY in your .env
  Your bot auto-pushes verified P&L to the marketplace

Docs: https://algochains.ai/docs/metrics-verification

— Tyler
```

#### Email 4: Revenue Model (Day 7)
**Subject:** How developers earn on AlgoChains

```
Hey {first_name},

Let's talk money. Here's how the revenue model works:

- You set a monthly subscription price ($29-$299/mo typical)
- AlgoChains takes a 20% platform fee
- You keep 80% of every subscription
- Payments via Stripe, monthly payouts

Example: A Gold-tier bot at $49/mo with 100 subscribers
= $49 × 100 × 80% = $3,920/mo passive income

The highest-performing bots on our platform:
- MNQ Momentum Scalper: OOS Sharpe 4.61 (Platinum)
- CL Energy Scalper: OOS Sharpe 3.12 (Gold)
- MES Swing Trader: OOS Sharpe 2.88 (Gold)

Higher tier = more visibility = more subscribers.

Ready to list? https://algochains.ai/dashboard/listings/new

— Tyler
```

#### Email 5: Community + Support (Day 14)
**Subject:** Join 200+ algo developers building on AlgoChains

```
Hey {first_name},

Quick check-in. How's your first strategy doing?

Resources if you're stuck:
- Discord: https://discord.gg/algochains (#dev-help channel)
- Docs: https://algochains.ai/docs
- GitHub: https://github.com/AlgoChains/algochains-mcp-server
- Office hours: Thursdays 2pm PT (Tyler + team)

What other developers are building:
- Forex breakout strategies (GBPUSD, EURUSD)
- Equity momentum (QQQ, SPY baskets)
- Crypto mean reversion
- Multi-asset portfolio optimizers

We're also accepting strategies built in:
- Python, Rust, C++
- QuantConnect LEAN
- Any language via MCP server

Ship something. We'll help you validate it.

— Tyler
```

### Developer Engagement Channels
- **Discord:** #dev-help, #strategy-ideas, #show-your-bot
- **GitHub Discussions:** Technical Q&A, feature requests
- **Weekly Office Hours:** Thursday 2pm PT (live Zoom)
- **Monthly Newsletter:** Top performers, new features, platform updates
- **Twitter/X:** @AlgoChains — strategy insights, platform updates

---

## Part 2: Consumer Onboarding (Subscriber Side)

### Target Audience
- Retail traders who want automated trading without coding
- Self-directed investors looking for validated strategies
- People tired of unverified "signal groups" and "copy trading"
- Registered at algochains.ai/io waitlist as consumers

### Onboarding Funnel

```
Waitlist Signup → Welcome Email (Day 0)
    → Marketplace Tour (Day 1)
    → "How Verification Works" (Day 3)
    → First Paper Subscription (Day 5)
    → Broker Connection Guide (Day 7)
    → Live Trading (Day 14)
    → Performance Dashboard (Day 30)
```

### Email Sequence

#### Email 1: Welcome (Day 0 — Immediate)
**Subject:** Your AI-powered trading bots are ready.

```
Hey {first_name},

Welcome to AlgoChains — the marketplace for verified trading bots.

Unlike signal groups or copy trading, every bot on AlgoChains is:
✅ Backtested on 4+ years of data
✅ Validated with statistical significance testing
✅ Paper traded for 30+ days before going live
✅ Metrics verified with broker fill confirmations

Browse the marketplace: https://algochains.ai/marketplace

Top bots right now:
- MNQ Momentum Scalper — Sharpe 4.61 (Platinum tier)
- CL Energy Scalper — Sharpe 3.12 (Gold tier)
- MES Swing Trader — Sharpe 2.88 (Gold tier)

Start with paper trading — no risk, real market data.

— Tyler, Founder @ AlgoChains
```

#### Email 2: Marketplace Tour (Day 1)
**Subject:** How to pick your first bot (and what the numbers mean)

```
Hey {first_name},

The marketplace can look overwhelming at first. Here's what matters:

📊 OOS Sharpe Ratio — risk-adjusted returns (higher = better)
  - Platinum (4.0+): Elite. Top 5% of strategies.
  - Gold (2.0-4.0): Strong. Consistently profitable.
  - Silver (1.0-2.0): Solid. Beats buy-and-hold.

📉 Max Drawdown — worst peak-to-trough decline
  - Under 15%: Conservative. Good for beginners.
  - 15-30%: Moderate. Standard for active strategies.
  - Over 30%: Aggressive. Higher risk, higher reward.

🔢 Trade Count — how many trades the strategy makes
  - 50-200: Swing trading (days-weeks)
  - 200-1000: Day trading (intraday)
  - 1000+: Scalping (minutes)

🏅 Tier Badge — our quality guarantee
  Platinum > Gold > Silver > Bronze

Start here: https://algochains.ai/marketplace?sort=sharpe

— Tyler
```

#### Email 3: Verification Deep Dive (Day 3)
**Subject:** Why you can trust these numbers (and how to verify yourself)

```
Hey {first_name},

Every bot on AlgoChains goes through 6 validation gates before listing:

Gate 1: Schema check (data integrity)
Gate 2: Performance thresholds (Sharpe ≥ 1.0, 50+ trades)
Gate 3: Overfitting detection (IS/OOS consistency)
Gate 4: MCPT statistical test (p < 0.05, not random luck)
Gate 5: Walk-forward validation (4+ time periods)
Gate 6: Paper trading graduation (30 days live market)

You can verify any bot's metrics yourself:
1. Click "Verify" on any listing page
2. See the full validation chain
3. Check broker-confirmed fill IDs
4. Download the SHA-256 hashed trade log

No other platform does this. We built it because we were tired
of fake screenshots and cherry-picked results.

Explore: https://algochains.ai/marketplace

— Tyler
```

#### Email 4: Setup Guide (Day 5)
**Subject:** Connect your broker in 5 minutes

```
Hey {first_name},

Ready to deploy a bot? Here's how:

Option A: One-Click Cloud Deploy (easiest)
1. Subscribe to a bot on the marketplace
2. Connect your broker via OAuth (Alpaca, Schwab, etc.)
3. Choose paper or live mode
4. Done — bot runs in our cloud

Option B: Local Deploy (more control)
1. pip install algochains-mcp-server
2. Add your broker API keys to .env
3. Subscribe via your AI agent
4. Bot runs on YOUR machine (keys never leave)

Supported brokers:
Alpaca, Interactive Brokers, Oanda, Schwab (via TradersPost),
Robinhood, Tastytrade, TradeStation, Coinbase, Kraken, and more.

Start with paper trading: https://algochains.ai/marketplace

— Tyler
```

#### Email 5: Performance Tracking (Day 14)
**Subject:** Your bot dashboard is live

```
Hey {first_name},

If you've subscribed to a bot, your dashboard now shows:

📈 Real-time P&L tracking
📊 Daily/weekly/monthly performance charts
🔔 Trade notifications (email + push)
⚡ Position updates
🛡️ Risk metrics (drawdown, exposure, VaR)

Access: https://algochains.ai/dashboard

Pro tips:
- Set max daily loss alerts ($X per day)
- Enable "pause on drawdown" for risk management
- Compare your bot's live performance vs backtest
- Export trade logs for tax reporting

Questions? Hit reply or join Discord.

— Tyler
```

### Consumer Engagement Channels
- **Email:** Drip sequence above + monthly performance digest
- **Dashboard:** Real-time P&L, trade history, alerts
- **Discord:** #general, #bot-reviews, #help
- **Push Notifications:** Trade fills, daily P&L summary
- **Monthly Report:** Portfolio performance, market conditions

---

## Part 3: Launch Sequence

### Pre-Launch (Week -2 to -1)
1. Finalize MCP server V3 (auth, deployment, verification)
2. Seed marketplace with 6 live bots (Tyler's portfolio)
3. Test subscriber flow end-to-end (sign up → subscribe → deploy)
4. Set up email automation (Loops.so or Resend)
5. Prepare Discord with channels and welcome bot

### Soft Launch (Week 0)
1. Send welcome emails to first 50 waitlist signups
2. Monitor sign-up → activation funnel
3. Offer 1:1 onboarding calls for first 10 developers
4. Collect feedback aggressively (Discord + email replies)

### Scale Launch (Week 2-4)
1. Open waitlist in batches (50 → 200 → all)
2. Launch referral program (free month for referrals)
3. Publish case studies (developer earnings, subscriber returns)
4. Twitter/X launch announcement with demo video
5. ProductHunt launch

### Growth (Month 2+)
1. Developer hackathon (build a bot, win prizes)
2. Strategy competition (highest Sharpe wins)
3. Partner with trading communities (Reddit, Discord servers)
4. Content marketing (blog posts, YouTube tutorials)
5. SEO for "algorithmic trading marketplace", "verified trading bots"

---

## Metrics to Track

### Developer Side
- **Waitlist → Signup:** Target 40%
- **Signup → First Submission:** Target 25%
- **Submission → Validated:** Target 60%
- **Validated → Listed:** Target 80%
- **Listed → First Subscriber:** Target 30% within 30 days

### Consumer Side
- **Waitlist → Signup:** Target 50%
- **Signup → Browse:** Target 80%
- **Browse → Paper Subscribe:** Target 20%
- **Paper → Live Subscribe:** Target 40%
- **Monthly Churn:** Target < 10%

### Platform Health
- **Active Listings:** 20+ within 60 days
- **Monthly Subscribers:** 100+ within 90 days
- **Platform GMV:** $10K+/mo within 90 days
- **NPS Score:** 50+ (both sides)

---

## Tools & Infrastructure

| Tool | Purpose | Status |
|------|---------|--------|
| **Supabase** | Auth (Google SSO), user DB, RLS | Ready |
| **Django REST** | Marketplace API, listings, subscriptions | Deployed |
| **MCP Server** | AI agent integration, broker connectivity | V3 Ready |
| **Resend / Loops** | Transactional + drip emails | To configure |
| **Discord** | Community, support, engagement | To set up |
| **Stripe** | Payments, subscriptions, payouts | To integrate |
| **Vercel Analytics** | Funnel tracking, conversion metrics | To configure |

---

*Last updated: July 2025*
*Owner: Tyler Reynolds (@tyler)*
