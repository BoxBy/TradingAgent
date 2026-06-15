import asyncio
import json
from core.agent import TradingAgentCore
from teams.task_manager import task_manager_instance, register_task_tools
from teams.teammate import TeammateAgent

class OrchestratorAgent:
    def __init__(self, system_prompt: str, mcp_bridge=None):
        from config import get_fallback_config
        fallback_config = get_fallback_config()
        self.agent = TradingAgentCore(system_prompt=system_prompt, fallback_config=fallback_config)

        self.mcp_bridge = mcp_bridge
        self.teammates = []

    async def register_mcp_tools(self):
        if not self.mcp_bridge: return
        tools = await self.mcp_bridge.get_openai_tools()
        for tool in tools:
            async def make_handler(tool_name):
                async def handler(name, args):
                    return await self.mcp_bridge.call_tool(tool_name, args)
                return handler
            
            handler = await make_handler(tool["function"]["name"])
            self.agent.add_tool(tool, handler)

    def spawn_teammates(self, count: int, system_prompt: str):
        """Spawns Teammate workers to process the shared task queue."""
        for i in range(count):
            tm = TeammateAgent(f"Teammate-{i+1}", system_prompt, self.mcp_bridge)
            # Share orchestrator's fallback config with teammates
            tm.agent.fallback_config = self.agent.fallback_config
            self.teammates.append(tm)

    async def run(self, user_objective: str):
        """Main execution loop for the Orchestrator."""

        # Persistent Context (Option B): Keep history to allow Serena compression at 150k threshold
        # self.agent.messages = [self.agent.messages[0]] if self.agent.messages else []

        # 1. Initialize MCP tools - Handled by scoping in main.py
        # await self.register_mcp_tools()
        # for tm in self.teammates:
        #     await tm.register_mcp_tools()

        # Shared completion signal
        done_event = asyncio.Event()

        async def bounded_work_loop(tm):
            """Work loop that exits when global done_event is set."""
            print(f"[{tm.name}] Ready - Waiting for tasks...")
            while not done_event.is_set():
                task = await task_manager_instance.get_claimable_task(tm.name)
                if not task:
                    await asyncio.sleep(2)
                    continue
                task_id = task["id"]
                print(f"[{tm.name}] Claimed Task: [{task_id[:8]}] {task['title']}")
                prompt = (
                    f"Please complete the following task:\nTitle: {task['title']}\n"
                    f"Description: {task['description']}\n\n"
                    "When you are finished, use the TaskUpdate tool to set the status to 'completed' and provide the result."
                )
                result_str = await tm.agent.run_turn(prompt)
                print(f"[{tm.name}] LLM execution completed: [{task_id[:8]}]")
                current_status = await task_manager_instance.get_task(task_id)
                if '"status": "in progress"' in current_status:
                    await task_manager_instance.update_task(task_id, status="completed", result=result_str)
                print(f"[{tm.name}] ✅ Task [{task_id[:8]}] Completed")

        async def orchestrate():
            try:
                print("[Orchestrator] Decomposing objective into tasks...")
                prompt = (
                    f"User Objective: {user_objective}\n"
                    "Please break down this objective into independent and dependent tasks using the TaskCreate tool. "
                    "Set appropriate 'blocked_by' dependencies to ensure logical execution flow. "
                    "STRATEGIC AUTONOMY: Ensure that the 'Market Condition' analysis task outputs cycle-level TP/SL recommendations, and that these recommendations are EXPLICITLY passed into the description of the 'Trade Execution' task so the RiskExecuter can apply them via `finalize_batch_decision`. "
                    "MANDATE: You are a fully autonomous quantitative system. Ensure your tasks lead to DIRECT EXECUTION of trades via RiskExecuter. "
                    "Do NOT create tasks that 'propose' or 'wait for approval'. Every trade with high conviction MUST be executed automatically. "
                    "Use TaskList to verify your created tasks. "
                    "When all tasks are created, provide a summary statement."
                )
                orchestration_result = await self.agent.run_turn(prompt)
                print(f"[Orchestrator] Decomposition completed: {orchestration_result}")
                
                from core.notification import send_notification
                send_notification(f"📝 *Objective Decomposed*\n{orchestration_result}", source="Orchestrator", category="decomposition")

                print("[Orchestrator] Waiting for teammates to complete tasks...")
                max_wait = 600
                elapsed = 0
                while elapsed < max_wait:
                    await asyncio.sleep(5)
                    elapsed += 5
                    tasks_json = await task_manager_instance.list_tasks()
                    tasks = json.loads(tasks_json)
                    if tasks and all(t["status"] == "completed" for t in tasks):
                        print("[Orchestrator] ✅ All tasks completed!")
                        break
                else:
                    print("[Orchestrator] ⚠️ Timeout - Some tasks were not completed in time. Proceeding to final report.")
                    incomplete = [t for t in tasks if t.get("status") != "completed"]
                    if tasks and len(incomplete) == len(tasks):
                        # Zero tasks completed — escalate
                        try:
                            from core.error_escalation import escalate_error
                            escalate_error(
                                'Cycle Zero Tasks Completed',
                                f'All {len(tasks)} tasks stuck (none completed) in 600s. '
                                f'Likely LLM connection error. Tasks: {[t.get("id","?")[:8] for t in incomplete[:5]]}',
                            )
                        except Exception:
                            pass
            finally:
                # Signal teammates to stop even if orchestration fails
                done_event.set()

            # Compile final report
            print("[Orchestrator] Generating final report...")
            final_prompt = (
                "All tasks have been processed. Please provide a FINAL SESSION REPORT for the user. "
                "Explicitly describe the ACTIONS TAKEN (buys/sells executed). "
                "If any trades were executed, highlight them. If a task failed due to timeout or LLM error, "
                "report it as a system bottleneck but emphasize that the system remains fully autonomous."
            )
            return await self.agent.run_turn(final_prompt)

        # Run orchestrator + all teammate loops concurrently
        # We don't return_exceptions=True here so that task errors are visible and stop the gather
        results = await asyncio.gather(
            orchestrate(),
            *[bounded_work_loop(tm) for tm in self.teammates]
        )
        
        # First result is from orchestrate()
        return results[0] if results else "No report generated."


    def calculate_vix_weights(self, vix_index: float) -> tuple[float, float]:
        """Calculates dynamic reviewer vs buyer weights using a Sigmoid function based on market VIX."""
        import math
        VIX_MIDPOINT = 25.0  
        VIX_STEEPNESS = 0.2    
        MIN_REVIEWER_WEIGHT = 0.25 
        MAX_REVIEWER_WEIGHT = 0.75 

        weight_range = MAX_REVIEWER_WEIGHT - MIN_REVIEWER_WEIGHT
        sigmoid_value = 1 / (1 + math.exp(-VIX_STEEPNESS * (vix_index - VIX_MIDPOINT)))
        
        reviewer_weight = MIN_REVIEWER_WEIGHT + (weight_range * sigmoid_value)
        buyer_weight = 1.0 - reviewer_weight
        return buyer_weight, reviewer_weight

    def format_comprehensive_payload(self, all_stocks_data: dict) -> str:
        """Assembles the heavy comprehensive_analyses context for HeadTrader."""
        comprehensive_analyses = {}
        for stock_code, data in all_stocks_data.items():
            comprehensive_analyses[stock_code] = {
                "technical": data.get("technical_indicator_analysis", {}),
                "sentiment": data.get("sentiment_analysis", {}),
                "fundamental": data.get("fundamental_analysis", {}),
                "qualitative": data.get("qualitative_analysis", {}),
                "chart_pattern": data.get("chart_pattern_analysis", {}),
                "avg_conviction": data.get("avg_conviction", 5.0)
            }
        return json.dumps(comprehensive_analyses, default=str)

    def allocate_budget_softmax(self, buy_decisions: list, total_investment_amount: float, ohlcv_data: dict, tau: float = 2.0) -> list:
        """
        Distributes total_investment_amount across buy_decisions using Softmax on conviction_score.
        Updates the 'quantity' field in the decisions based on current stock prices in ohlcv_data.
        """
        import math
        if not buy_decisions:
            return []

        exps = []
        for d in buy_decisions:
            exps.append(math.exp((d.get("conviction_score", 5.0)) / tau))
        
        denom = sum(exps) or 1.0

        for idx, decision in enumerate(buy_decisions):
            stock_code = decision.get("stock_code")
            # Retrieve price from provided OHLCV dict
            df = ohlcv_data.get(stock_code)
            stock_price = 0
            if df is not None and not df.empty:
                stock_price = df.iloc[-1]["Close"]

            weight = exps[idx] / denom
            investment_per_stock = total_investment_amount * weight
            
            # Enforce max investment per stock rule from config
            from config import USER_RULES
            max_per_stock = USER_RULES.get("max_investment_per_stock", 100000000)
            investment_per_stock = min(investment_per_stock, max_per_stock)

            decision["quantity"] = int(investment_per_stock / stock_price) if stock_price > 0 else 0
            decision["allocated_amount"] = investment_per_stock
            
        return buy_decisions

