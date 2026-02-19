# PaySponge.com - Complete Website Documentation

**Documentation Date:** February 18, 2026  
**Website:** https://paysponge.com/

---

## Executive Summary

**Sponge** (paysponge.com) is a financial infrastructure platform designed specifically for the AI agent economy. It provides crypto wallets and payment capabilities that enable AI agents to autonomously hold, spend, transfer, and earn money across multiple blockchain networks, while also enabling businesses to sell directly to AI agents without human intervention.

---

## Main Product Description

### What is Sponge?

Sponge provides crypto wallets designed specifically for AI agents. The platform handles all the complexity of multi-chain wallet management, allowing AI agents to:

- Hold and manage cryptocurrency
- Send and receive payments
- Swap tokens
- Pay for services and APIs
- Invest and earn money
- Operate autonomously with fiat and crypto

The platform consists of two main components:
1. **Wallet System** - For agents to hold and spend money
2. **Gateway System** - For businesses to sell directly to agents

---

## Core Features

### 1. Wallets for AI Agents

**Key Capabilities:**
- **Multi-chain Support** - Each agent receives:
  - One EVM wallet (works across Ethereum, Base, and all EVM chains)
  - One Solana wallet (works on Solana mainnet and devnet)
- **Secure by Default** - API keys scoped per agent with:
  - Spending limits
  - Address allowlists
  - Audit logging
- **Simple SDK** - Clean TypeScript API for authentication, wallet creation, and transactions
- **First-class Claude Integration** - Native support for Anthropic's Model Context Protocol (MCP) and direct tool calling

### 2. Gateway for Businesses

Sponge Gateway enables businesses to:
- Accept payments from AI agents without human interaction
- Onboard agents easily
- Sell services directly to agents with a few clicks
- No code changes required

### 3. Developer-Focused SDK

**Integrations:**
- TypeScript SDK (`@spongewallet/sdk`)
- MCP (Model Context Protocol) server
- Direct tool calling for Anthropic's Claude

**Compatible with:**
- Claude (via MCP and direct tools)
- OpenClaw
- Claude Code
- Codex
- Any MCP-compatible client

---

## Supported Blockchain Networks

### Mainnet Chains (Live Keys Only)

| Chain | Chain ID | Type | Description |
|-------|----------|------|-------------|
| Ethereum | 1 | EVM | Original smart contract platform |
| Base | 8453 | EVM | Coinbase's L2 with low fees |
| Solana | 101 | Solana | High-performance blockchain |

**Supported Tokens:**
- **Ethereum:** ETH (native), USDC
- **Base:** ETH (native), USDC
- **Solana:** SOL (native), USDC, any SPL token

### Testnet Chains (Test Keys Only)

| Chain | Chain ID | Type | Description |
|-------|----------|------|-------------|
| Sepolia | 11155111 | EVM | Ethereum testnet |
| Base Sepolia | 84532 | EVM | Base testnet |
| Solana Devnet | 102 | Solana | Solana development network |
| Tempo | 42431 | EVM | Fast testnet with instant finality |

---

## Technical Architecture

### Components

1. **SpongeWallet** - Primary SDK class for wallet operations and Claude integration
2. **SpongeAdmin** - Admin SDK for programmatic agent creation with master keys
3. **MCP Server** - Model Context Protocol server for Claude Desktop and MCP clients

### API Keys

**Two types of API keys:**

| Key Type | Prefix | Chains | Purpose |
|----------|--------|--------|---------|
| Test | `sponge_test_` | Testnets only | Development and testing |
| Live | `sponge_live_` | Mainnets only | Production use |

**Key Features:**
- Strictly scoped to test or live environments
- Cannot be mixed (test keys cannot access mainnet, live keys cannot access testnet)
- Per-agent scoping
- Configurable spending limits
- Address allowlisting
- Comprehensive audit logging

---

## SDK Installation & Quickstart

### Installation

```bash
# Using bun
bun add @spongewallet/sdk

# Using npm
npm install @spongewallet/sdk

# Using yarn
yarn add @spongewallet/sdk
```

### Basic Usage Example

```typescript
import { SpongeWallet } from "@spongewallet/sdk";

// Connect (handles authentication automatically)
const wallet = await SpongeWallet.connect();

// Get wallet addresses
const addresses = await wallet.getAddresses();
// { evm: "0x...", solana: "7nYB..." }

// Check balances across all chains
const balances = await wallet.getBalances();

// Transfer crypto
const tx = await wallet.transfer({
  chain: "base",
  to: "0xRecipientAddress",
  amount: "10.0",
  currency: "USDC"
});

console.log(`Transaction: ${tx.hash}`);
console.log(`Explorer: ${tx.explorerUrl}`);
```

---

## Claude Integration Methods

### Method 1: MCP Integration (Recommended)

```typescript
import Anthropic from "@anthropic-ai/sdk";
import { SpongeWallet } from "@spongewallet/sdk";

const wallet = await SpongeWallet.connect();
const anthropic = new Anthropic();

const response = await anthropic.messages.create({
  model: "claude-sonnet-4-20250514",
  max_tokens: 1024,
  messages: [{ role: "user", content: "What's my wallet balance?" }],
  mcp_servers: {
    wallet: wallet.mcp()
  }
});
```

### Method 2: Direct Tools

```typescript
const tools = wallet.tools();

const response = await anthropic.messages.create({
  model: "claude-sonnet-4-20250514",
  max_tokens: 1024,
  messages: [{ role: "user", content: "Send 1 USDC to 0x..." }],
  tools: tools.definitions
});

// Execute tool calls
for (const block of response.content) {
  if (block.type === "tool_use") {
    const result = await tools.execute(block.name, block.input);
    // Continue conversation with result
  }
}
```

### Claude Desktop Setup

Add to MCP configuration:

```json
{
  "mcpServers": {
    "sponge-wallet": {
      "command": "npx",
      "args": ["@spongewallet/mcp", "--api-key", "YOUR_API_KEY"]
    }
  }
}
```

Or with environment variables:

```json
{
  "mcpServers": {
    "sponge-wallet": {
      "command": "npx",
      "args": ["@spongewallet/mcp"],
      "env": {
        "SPONGE_API_KEY": "sponge_test_..."
      }
    }
  }
}
```

---

## Available MCP Tools for Claude

### Wallet Tools
- `get_balance` - Check balance on one or all chains
- `evm_transfer` - Transfer ETH/USDC on Ethereum or Base
- `solana_transfer` - Transfer SOL/USDC on Solana
- `tempo_transfer` - Transfer pathUSD on Tempo (testnet)
- `solana_swap` - Swap tokens on Solana via Jupiter
- `get_solana_tokens` - List all SPL tokens in wallet
- `search_solana_tokens` - Search Jupiter token database

### Transaction Tools
- `get_transaction_status` - Check transaction status
- `get_transaction_history` - View past transactions

### Fund Management
- `request_funding` - Request funds from owner
- `withdraw_to_main_wallet` - Withdraw to owner's wallet
- `request_tempo_faucet` - Get testnet tokens (test keys)

---

## Authentication Methods

### 1. Device Flow (Recommended for Interactive Apps)

Follows OAuth 2.0 Device Authorization Grant (RFC 8628):

```typescript
const wallet = await SpongeWallet.connect({
  testnet: true,                    // Request test or live key
  agentName: "My Trading Bot",      // Custom agent name
  onVerification: ({ verificationUri, userCode }) => {
    console.log(`Visit ${verificationUri} and enter: ${userCode}`);
  }
});
```

### 2. Manual API Key Usage

```typescript
const wallet = await SpongeWallet.fromApiKey("sponge_test_...");

// Or via environment variable
// SPONGE_API_KEY=sponge_test_...
const wallet = await SpongeWallet.connect();
```

### 3. Master Keys (For Programmatic Agent Creation)

Master keys enable platforms to create and manage multiple agents:

```typescript
import { SpongeAdmin } from "@spongewallet/sdk";

const admin = await SpongeAdmin.fromApiKey("master_xxx_...");

// Create a new agent
const agent = await admin.createAgent({
  name: "Trading Bot Alpha",
  description: "Automated trading agent",
  testnet: true,
  dailySpendingLimit: "100.0",
  weeklySpendingLimit: "500.0",
  monthlySpendingLimit: "1000.0"
});

// Use the agent's API key
const wallet = await SpongeWallet.fromApiKey(agent.apiKey);
```

---

## Security Features

### Spending Limits

Set various types of spending limits:

| Limit Type | Description |
|------------|-------------|
| `per_transaction` | Maximum per single transaction |
| `per_minute` | Rolling 1-minute limit |
| `hourly` | Rolling 1-hour limit |
| `daily` | Rolling 24-hour limit |
| `weekly` | Rolling 7-day limit |
| `monthly` | Rolling 30-day limit |

```typescript
await admin.setSpendingLimit("agent_id", {
  type: "per_transaction",
  amount: "100.0",
  currency: "USD"
});
```

### Address Allowlisting

Restrict transfers to approved addresses only:

```typescript
await admin.addToAllowlist("agent_id", {
  chain: "base",
  address: "0xTrustedAddress",
  label: "Treasury"
});
```

### Audit Logging

View all agent activity:

```typescript
const logs = await admin.getAgentAuditLogs("agent_id", {
  limit: 50
});
```

### API Key Scopes

| Scope | Description |
|-------|-------------|
| `wallet:read` | Read wallet addresses and balances |
| `wallet:write` | Create and manage wallets |
| `transaction:read` | View transaction history |
| `transaction:sign` | Sign transactions |
| `transaction:write` | Submit transactions |
| `spending:read` | View spending limits |
| `flow:execute` | Execute automated flows |
| `payment:read` | Read payment methods |
| `payment:decrypt` | Decrypt stored cards |
| `payment:write` | Update payment records |

---

## Wallet Operations

### Getting Addresses

```typescript
// Get all addresses
const addresses = await wallet.getAddresses();
// { evm: "0x...", solana: "7nYB..." }

// Get address for specific chain
const baseAddress = await wallet.getAddress("base");
```

**Note:** EVM address is the same across all EVM chains (Ethereum, Base, etc.)

### Checking Balances

```typescript
// All chains
const balances = await wallet.getBalances();

// Single chain
const baseBalance = await wallet.getBalance("base");

// Solana tokens
const tokens = await wallet.getSolanaTokens();
```

### Transfers

```typescript
// EVM transfer (Ethereum, Base)
const tx = await wallet.transfer({
  chain: "base",
  to: "0xRecipientAddress",
  amount: "50.0",
  currency: "USDC"
});

// Solana transfer
const solTx = await wallet.transfer({
  chain: "solana",
  to: "SolanaAddress",
  amount: "1.0",
  currency: "SOL"
});
```

### Token Swaps (Solana Only)

Uses Jupiter aggregator:

```typescript
// Get quote first
const quote = await wallet.getSwapQuote({
  chain: "solana",
  fromToken: "SOL",
  toToken: "USDC",
  amount: "1.0"
});

// Execute swap
const swap = await wallet.swap({
  chain: "solana",
  fromToken: "SOL",
  toToken: "USDC",
  amount: "1.0",
  slippage: 0.5  // 0.5%
});
```

### Transaction History

```typescript
const history = await wallet.getTransactionHistory({
  limit: 10,
  chain: "base"  // Optional filter
});
```

### Transaction Status

```typescript
// EVM transaction
const status = await wallet.getTransactionStatus({
  hash: "0xabc123...",
  chain: "base"
});

// Solana transaction
const solStatus = await wallet.getTransactionStatus({
  signature: "5UyZbK...",
  chain: "solana"
});
```

---

## Agent Management (Admin SDK)

### Creating Agents

```typescript
import { SpongeAdmin } from "@spongewallet/sdk";

const admin = await SpongeAdmin.fromApiKey("master_xxx_...");

const agent = await admin.createAgent({
  name: "My Agent",
  description: "Agent description",
  testnet: true,
  dailySpendingLimit: "100.0",
  metadata: {
    environment: "production"
  }
});
```

### Managing Agents

```typescript
// List all agents
const agents = await admin.listAgents({
  includeBalances: true,
  testMode: true
});

// Get agent details
const agent = await admin.getAgent("agent_id");

// Pause/Resume
await admin.pauseAgent("agent_id");
await admin.resumeAgent("agent_id");

// Delete
await admin.deleteAgent("agent_id");

// Rotate API key
const newKey = await admin.rotateAgentKey("agent_id");
```

---

## Use Cases Shown on Website

### 1. Stock Trading
```
Buy 10 shares of Acme Inc if their revenue growth exceeds 50%
```
Agent can purchase financial data from paid APIs and execute trades autonomously.

### 2. Payments
```
Send $150 to [email protected] for logo deliverables
```
Agent can make direct payments to freelancers or services.

### 3. Spending Controls
```
Set a $25/day budget, $5/tx limit, and only allow ai-assist.dev, data-api.dev, webscrape.dev
```
Agent operates within strict financial guardrails.

---

## Error Handling

### Error Types

```typescript
import {
  SpongeError,
  InsufficientFundsError,
  InvalidAddressError,
  SpendingLimitError,
  AllowlistError
} from "@spongewallet/sdk";

try {
  await wallet.transfer({...});
} catch (error) {
  if (error instanceof InsufficientFundsError) {
    console.log("Not enough funds:", error.available, error.required);
  } else if (error instanceof InvalidAddressError) {
    console.log("Invalid recipient address");
  }
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `UNAUTHORIZED` | Invalid or expired API key |
| `INSUFFICIENT_FUNDS` | Not enough balance |
| `INVALID_ADDRESS` | Invalid recipient address |
| `INVALID_CHAIN` | Chain not available for key type |
| `SPENDING_LIMIT_EXCEEDED` | Transaction exceeds limit |
| `ADDRESS_NOT_ALLOWLISTED` | Recipient not on allowlist |
| `AGENT_PAUSED` | Agent is paused |
| `RATE_LIMITED` | Too many requests |
| `NETWORK_ERROR` | Blockchain network error |

---

## Testnet Features

### Free Testnet Tokens

```typescript
// Request testnet tokens (test keys only)
const faucet = await wallet.requestFaucet({ chain: "tempo" });
console.log(`Received ${faucet.amount} ${faucet.symbol}`);
```

### Available Test Chains
- **Sepolia** - Ethereum testnet
- **Base Sepolia** - Base testnet
- **Solana Devnet** - Solana testnet
- **Tempo** - Fast testnet with instant finality

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SPONGE_API_KEY` | Default agent API key |
| `SPONGE_MASTER_KEY` | Default master key |
| `SPONGE_DEBUG` | Enable debug logging |
| `SPONGE_API_URL` | Custom API URL (advanced) |

---

## Block Explorers

| Chain | Explorer |
|-------|----------|
| Ethereum | etherscan.io |
| Base | basescan.org |
| Solana | solscan.io |
| Sepolia | sepolia.etherscan.io |
| Base Sepolia | sepolia.basescan.org |
| Solana Devnet | solscan.io?cluster=devnet |

---

## Website Navigation Structure

### Main Pages
- **Homepage** (`/`) - Overview, value proposition, use cases
- **Docs** (`/docs`) - Complete developer documentation
- **Wallet** (`https://wallet.paysponge.com/`) - Wallet interface
- **Gateway** (`https://gateway.paysponge.com/`) - Gateway interface
- **About** (`/about`) - Simple about page

### Documentation Pages
1. **Overview** (`/docs`) - Main documentation hub
2. **Quickstart** (`/docs/quickstart`) - Get started in 5 minutes
3. **Claude Integration** (`/docs/claude-integration`) - MCP and direct tools
4. **Authentication** (`/docs/authentication`) - Device flow, API keys, master keys
5. **API Reference** (`/docs/api-reference`) - Complete SDK reference
6. **Master Keys** (`/docs/master-keys`) - Programmatic agent management
7. **Wallets** (`/docs/wallets`) - Wallet operations and transfers
8. **Chains** (`/docs/chains`) - Supported blockchain networks

---

## Important URLs Visited

1. https://paysponge.com/ - Homepage
2. https://paysponge.com/docs - Documentation hub
3. https://paysponge.com/docs/quickstart - Getting started guide
4. https://paysponge.com/docs/claude-integration - Claude integration methods
5. https://paysponge.com/docs/authentication - Authentication & API keys
6. https://paysponge.com/docs/api-reference - Complete API reference
7. https://paysponge.com/docs/master-keys - Master key management
8. https://paysponge.com/docs/wallets - Wallet operations
9. https://paysponge.com/docs/chains - Supported chains
10. https://wallet.paysponge.com/ - Wallet interface
11. https://gateway.paysponge.com/ - Gateway interface

---

## Pricing Information

**Not Found** - No pricing page or pricing information was found on the website. The service appears to be in early stages, possibly in beta or free tier. Users would need to contact the team or check the platform after signing up for pricing details.

---

## Key Differentiators

1. **Agent-First Design** - Built specifically for AI agents, not adapted from consumer wallets
2. **Multi-Chain Native** - Seamless support for multiple blockchains with unified API
3. **Claude Integration** - First-class MCP support and direct tool integration
4. **Security Controls** - Built-in spending limits, allowlists, and audit logging
5. **Developer Experience** - Simple TypeScript SDK, comprehensive documentation
6. **Test & Live Separation** - Strict key scoping prevents accidental mainnet usage during development
7. **Instant Testnet** - Tempo chain for rapid development iteration
8. **Master Keys** - Platform-friendly API for creating multiple agent wallets
9. **Gateway System** - Enable businesses to sell to agents autonomously

---

## Technical Specifications

### SDK Package
- **Package Name:** `@spongewallet/sdk`
- **MCP Package:** `@spongewallet/mcp`
- **Language:** TypeScript
- **Compatible With:** Node.js, Bun

### Supported Claude Models
- claude-sonnet-4-20250514 (and compatible models)

### Integration Methods
- Model Context Protocol (MCP)
- Direct tool calling via Anthropic SDK
- Standalone TypeScript/JavaScript SDK

### Chain Support
- **EVM Chains:** Ethereum, Base, Sepolia, Base Sepolia, Tempo
- **Solana:** Mainnet, Devnet
- **DEX Integration:** Jupiter (Solana)

---

## Summary

**Sponge** is a comprehensive financial infrastructure platform designed for the emerging AI agent economy. It provides the essential building blocks for AI agents to participate in economic transactions autonomously:

- **Multi-chain crypto wallets** with EVM and Solana support
- **Simple TypeScript SDK** for easy integration
- **Native Claude integration** via MCP and direct tools
- **Robust security controls** including spending limits and allowlists
- **Gateway system** for businesses to sell to agents
- **Admin tools** for managing multiple agents
- **Comprehensive documentation** and developer resources

The platform bridges the gap between AI agents and financial transactions, enabling agents to hold money, make payments, swap tokens, and interact with paid services—all while maintaining strict security controls and audit trails.

---

**End of Documentation**
