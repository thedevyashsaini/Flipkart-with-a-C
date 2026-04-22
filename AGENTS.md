## 🧠 Project: *Flipkart with a C*

## Hackathon problem statement 

title - Smart Supply Chains
subtitle - Resilient Logistics and Dynamic Supply Chain Optimization

description - Modern global supply chains manage millions of concurrent shipments across highly complex and inherently volatile transportation networks. Critical transit disruptions ranging from sudden weather events to hidden operational bottlenecks are chronically identified only after delivery timelines are already compromised.

objective - Design a scalable system capable of continuously analyzing multifaceted transit data to preemptively detect and flag potential supply chain disruptions. Formulate dynamic mechanisms that instantly execute or recommend highly optimized route adjustments before localized bottlenecks cascade into broader delays.

### Core Philosophy

We’re approaching the problem not as simple route optimization, but as **managing flow in a dynamic, pressure-driven network**.

In our model:

* Warehouses, hubs, and airports are **nodes with limited capacity**
* Routes between them are **edges carrying flow (shipments)**

Instead of always pushing shipments along the shortest or cheapest path, the system:

* Monitors **node pressure**
* Tracks **edge stress**
* Detects **regional instability**
* Makes decisions to **maintain global system stability**

---

### 💡 Key Insight

> We are optimizing for **system stability under uncertainty**, not just per-shipment cost.

Example:

If a shipment must go from A → C:

* Baseline (without a c):

  * Picks A → C (cost = 4)
  * Ignores congestion
  * Can overload C → causes cascading failure

* Our system (with a c):

  * Routes A → B → C
  * Holds at B if C is congested
  * Waits for pressure to reduce
  * Sends when safe

➡️ This increases:

* local cost
* delivery time

But reduces:

* system-wide congestion
* cascading failures
* long-term cost

---

### 🚫 What we are NOT solving

We explicitly do **NOT** handle:

* micro-level routing (like trucks avoiding traffic)
* real-time vehicle navigation

That domain is already handled by tools like Google Maps.

Instead, we operate at:

> **inter-node decision level (macro logistics control)**

---

# 🧱 System Architecture

We structure the system into **6 core modules**.

---

## 1. 📦 Demand Generator

Generates synthetic shipment demand.

### Responsibilities:

* Create shipments with:

  * `source`
  * `destination`
  * `load`
  * `priority`
  * `deadline`
  * `SKU class`
* Control:

  * rate of generation
  * spatial distribution
  * burst patterns

### Goal:

Simulate realistic and controllable demand.

---

## 2. ⚡ Scenario Injector

Introduces stress into the system.

### Responsibilities:

* Increase load at nodes
* Reduce effective capacity
* Increase edge ETA
* Simulate:

  * demand spikes
  * congestion buildup
  * partial failures

### Important:

These are **hidden causes**.
Agents only observe resulting behavior (delays, queues).

---

## 3. 🔁 Simulator Core

The “physics engine” of the system.

### Responsibilities:

* Advance time in discrete ticks
* Move shipments along edges
* Enforce:

  * node capacity constraints
  * edge travel times
* Maintain:

  * queues
  * in-transit shipments

### Important:

* This module does **NOT make decisions**
* It only applies rules and evolves the system

---

## 4. 🤖 Agents

We have two agents:

---

### 🟥 without a c (Baseline)

* Optimizes for:

  * shortest path
  * lowest cost

### Behavior:

* Always pushes shipments forward
* Ignores congestion
* No holding, no anticipation

---

### 🟩 with a c (Our Agent)

* Optimizes for:

  * system stability
  * congestion avoidance
  * controlled flow

### Uses signals like:

* node pressure (derived)
* edge delay trends
* queue buildup
* upstream/downstream imbalance

---

### 🎯 Control Actions

Must-have:

* `hold` → delay shipment at current node
* `reroute` → choose alternate path
* `priority scheduling` → prefer critical shipments

Nice-to-have:

* `split flow` → divide shipments across paths

Optional:

* `throttle inflow` → control admission into congested nodes

---

### 🧠 Behavior Philosophy

> Don’t push flow into a failing node.

Instead:

* delay
* divert
* redistribute

---

## 5. 📊 Evaluator

Compares both agents on identical conditions.

### Critical Rule:

> Both agents MUST run on the same seed, demand, and disruptions.

---

### KPIs:

* On-time delivery %
* Peak node congestion
* Cascading impact (number of affected nodes)
* Recovery time
* Total cost delta

---

## 6. 🖥️ Dashboard + Replay

Visualization and storytelling layer.

### Features:

* Graph view of network
* Toggle:

  * `with a c`
  * `without a c`
* Time slider (space-time replay)
* Node interaction:

  * manually inject load
* Timeline markers:

  * where with-a-c would intervene

---

### Replay Concept

1. Run simulation with `without a c`
2. System degrades / fails
3. Mark critical intervention points
4. Replay same scenario with `with a c`
5. Show how system stabilizes

---

# 🧠 Core System Concepts

---

## Node Pressure

Derived from:

* queue size
* inflow vs outflow

Represents:

> how close a node is to failure

---

## Edge Stress

Derived from:

* current ETA vs baseline ETA

Represents:

> degradation in transit performance

---

## Backpressure

When:

* downstream node is congested
* upstream nodes start accumulating load

---

## Cascading Failure

When:

* one node fails
* causes overload in neighbors
* spreads across network

---

# 🧭 Design Principles

---

## 1. Separation of Concerns

* Dataset = raw facts
* Simulator = physics
* Agent = intelligence

---

## 2. No Explicit Labels

We do NOT provide:

* “this node is congested”
* “this is a failure”

Agents must **infer everything**

---

## 3. Determinism

* Same seed = same world
* Required for fair A/B comparison

---

## 4. Simplicity > Complexity

Avoid:

* heavy ML
* RL training
* unnecessary infra

Focus on:

* clear logic
* visible behavior

---

# 🏁 Final Outcome

We demonstrate that:

> A system optimizing only for cost can destabilize under stress.

Whereas:

> A pressure-aware system can sacrifice local optimality to preserve global stability.

---

## 🔥 Example Scenario

Graph:

* A → B = cost 3
* B → C = cost 2
* A → C = cost 4

### without a c:

* always picks A → C
* overloads C
* leads to cascading failure

---

### with a c:

* routes A → B → C
* holds at B if C is congested
* releases when safe

---

## Result:

| Metric             | without a c   | with a c        |
| ------------------ | ------------- | --------------- |
| Cost per shipment  | lower         | slightly higher |
| Delay              | unpredictable | controlled      |
| System stability   | poor          | strong          |
| Cascading failures | frequent      | avoided         |

---

# 🧠 Final Mental Model

> This is not routing.
> This is **flow control in a constrained, dynamic network**.

---
