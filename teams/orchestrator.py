import asyncio
import json
from core.agent import TradingAgentCore
from teams.task_manager import task_manager_instance, register_task_tools
from teams.teammate import TeammateAgent

class OrchestratorAgent:
    def __init__(self, system_prompt: str, mcp_bridge=None):
        self.agent = TradingAgentCore(system_prompt=system_prompt)
        register_task_tools(self.agent)
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
            self.teammates.append(tm)

    async def run(self, user_objective: str):
        """Main execution loop for the Orchestrator."""
        
        # 1. Initialize MCP tools
        await self.register_mcp_tools()
        for tm in self.teammates:
            await tm.register_mcp_tools()

        # Shared completion signal
        done_event = asyncio.Event()

        async def bounded_work_loop(tm):
            """Work loop that exits when global done_event is set."""
            print(f"[{tm.name}] 준비 완료 - 태스크 대기 중...")
            while not done_event.is_set():
                task = await task_manager_instance.get_claimable_task(tm.name)
                if not task:
                    await asyncio.sleep(2)
                    continue
                task_id = task["id"]
                print(f"[{tm.name}] 태스크 클레임: [{task_id[:8]}] {task['title']}")
                prompt = (
                    f"Please complete the following task:\nTitle: {task['title']}\n"
                    f"Description: {task['description']}\n\n"
                    "When you are finished, use the TaskUpdate tool to set the status to 'completed' and provide the result."
                )
                result_str = await tm.agent.run_turn(prompt)
                print(f"[{tm.name}] LLM 실행 완료: [{task_id[:8]}]")
                current_status = await task_manager_instance.get_task(task_id)
                if '"status": "in progress"' in current_status:
                    await task_manager_instance.update_task(task_id, status="completed", result=result_str)
                print(f"[{tm.name}] ✅ 태스크 [{task_id[:8]}] 완료")

        async def orchestrate():
            print("[Orchestrator] 태스크 분해 중...")
            prompt = (
                f"User Objective: {user_objective}\n"
                "Please break down this objective into independent and dependent tasks using the TaskCreate tool. "
                "Set appropriate 'blocked_by' dependencies to ensure logical execution flow. "
                "Use TaskList to verify your created tasks. "
                "When all tasks are created, provide a summary statement."
            )
            orchestration_result = await self.agent.run_turn(prompt)
            print(f"[Orchestrator] 분해 완료: {orchestration_result}")
            
            from core.notification import send_notification
            send_notification(f"📝 *목표 분해 완료*\n{orchestration_result}")

            print("[Orchestrator] 팀원들이 태스크를 완료하기를 기다리는 중...")
            max_wait = 600
            elapsed = 0
            while elapsed < max_wait:
                await asyncio.sleep(5)
                elapsed += 5
                tasks_json = await task_manager_instance.list_tasks()
                tasks = json.loads(tasks_json)
                if tasks and all(t["status"] == "completed" for t in tasks):
                    print("[Orchestrator] ✅ 모든 태스크 완료!")
                    break
            else:
                print("[Orchestrator] ⚠️ 타임아웃 - 기한 내 완료되지 않은 태스크가 있습니다. 리포트로 진행.")

            # Signal teammates to stop
            done_event.set()

            # Compile final report
            print("[Orchestrator] 최종 리포트 작성 중...")
            final_prompt = "All tasks have been completed. Please summarize the final results for the user."
            return await self.agent.run_turn(final_prompt)

        # Run orchestrator + all teammate loops concurrently
        results = await asyncio.gather(
            orchestrate(),
            *[bounded_work_loop(tm) for tm in self.teammates],
            return_exceptions=True
        )
        
        # First result is from orchestrate()
        final_report = results[0] if results else "No report generated."
        if isinstance(final_report, Exception):
            final_report = f"Orchestrator error: {final_report}"
        return final_report


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
            
            # Enforce max investment per stock rule
            max_per_stock = 100000000 # 100M KRW max
            investment_per_stock = min(investment_per_stock, max_per_stock)

            decision["quantity"] = int(investment_per_stock / stock_price) if stock_price > 0 else 0
            decision["allocated_amount"] = investment_per_stock
            
        return buy_decisions

