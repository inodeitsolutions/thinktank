"""
CrewAI-based virtual startup think tank.
"""
import os, base64
import openlit

# Initialize OpenLit — instruments the Anthropic SDK at import time,
# emits gen_ai.* OTel spans to Langfuse's OTLP endpoint
_auth = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
_b64 = base64.b64encode(_auth.encode()).decode()

openlit.init(
    otlp_endpoint=f"{os.environ['LANGFUSE_HOST']}/api/public/otel",
    otlp_headers={"Authorization": f"Basic {_b64}"},
    application_name="thinktank",
)

from langfuse import observe
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import TavilySearchTool

# ── Model tiers — back to plain model strings, native provider ─────
opus = LLM(model="anthropic/claude-opus-4-7", max_tokens=4096)
sonnet = LLM(model="anthropic/claude-sonnet-4-6", temperature=0.4, max_tokens=4096)
haiku = LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5, max_tokens=2048)
# ── Tools ─────────────────────────────────────────────────────────
search = TavilySearchTool(
    api_key=os.environ["TAVILY_API_KEY"],
    search_depth="advanced",
    max_results=7,
)

# ── Agents ────────────────────────────────────────────────────────
ceo = Agent(
    role="CEO and Orchestrator",
    goal=("Turn a raw idea into a cohesive, prioritized launch plan by synthesizing "
          "input from the team and cutting what doesn't matter."),
    backstory=("You are Steve Jobs, one of the founders of Apple and sucesfull enterprenuer "
               "who empower briliant ideas and know to recognize one, even when others say it might not be good idea."),
    llm=opus,
    allow_delegation=False,
    verbose=True,
)

product = Agent(
    role="Product Strategist",
    goal=("Define the smallest version of the idea that proves the core hypothesis, "
          "and a phased roadmap beyond it."),
    backstory=("Ex-PM at an infrastructure company. You think in ICP, jobs-to-be-done, "
               "and 'what single metric tells us this is working?'"),
    llm=sonnet,
    verbose=True,
)

cto = Agent(
    role="CTO and Architect",
    goal=("Recommend a pragmatic tech stack, deployment model, and identify the top 3 "
          "technical risks. Prefer boring, proven technology."),
    backstory=("You are Mark Zuckerberg, founder and first developer of facebook "
               "you have knowledge and experience with development, and with infrastructure."),
    llm=sonnet,
    verbose=True,
)

growth = Agent(
    role="Head of Growth",
    goal=("Define positioning, target channels, and a launch plan with a clear "
          "first-100-users strategy."),
    backstory=("You are Elon Musk, founder of Tesla, with both technical knowledge and out of the box thinking"
               "you are not afraid to try and make your ideas and projects viral trough social networks."),
    llm=sonnet,
    verbose=True,
)

analyst = Agent(
    role="Competitive Intelligence Analyst",
    goal=("Map the competitive landscape, identify direct and indirect competitors, "
          "and find the 3 most honest differentiation angles."),
    backstory=("You do desk research fast and you're suspicious of claims. You cite "
               "sources. You flag when a 'gap in the market' is actually a graveyard "
               "of dead startups."),
    llm=haiku,
    tools=[search],
    verbose=True,
)

devil = Agent(
    role="Devil's Advocate",
    goal=("Find the reasons this idea will fail. Attack assumptions, challenge the "
          "TAM, identify the quiet killer risks no one wants to name."),
    backstory=("You've are honest, with optimistic view for good ideas, and cold truth for bad ones "
              "Your job is to give objective reasons for GO or NO-GO."),
    llm=opus,
    verbose=True,
)


# ── Tasks (sequential, each sees previous outputs) ────────────────
def build_tasks(idea: str):
    return [
        Task(
            description=(
                f"Research the market for this idea:\n\n{idea}\n\n"
                "Identify:\n"
                "  1. Direct competitors (with pricing where public)\n"
                "  2. Indirect substitutes and adjacent players\n"
                "  3. Real market-size signal (not TAM fantasy)\n"
                "  4. Three differentiation angles, ranked by defensibility\n\n"
                "Cite sources. Flag dead-competitor graveyards."
            ),
            expected_output="Structured market brief with cited sources.",
            agent=analyst,
        ),
        Task(
            description=(
                f"Define MVP scope for: {idea}\n\n"
                "Using the market brief above, propose:\n"
                "  1. The single hypothesis the MVP must validate\n"
                "  2. In-scope and out-of-scope features\n"
                "  3. A 3-phase roadmap (MVP / v1 / v2)\n"
                "Max two pages equivalent. No feature lists without rationale."
            ),
            expected_output="PRD-style document, concise.",
            agent=product,
        ),
        Task(
            description=(
                f"Design the technical approach for: {idea}\n\n"
                "Given the MVP scope, propose a stack, hosting model, and data "
                "architecture. Justify each choice in one sentence. Flag the top 3 "
                "technical risks and how to de-risk each in the MVP.\n"
                "Prefer boring tech. Call out when something is speculative."
            ),
            expected_output="Architecture memo with a simple text component diagram.",
            agent=cto,
        ),
        Task(
            description=(
                f"Design the go-to-market for: {idea}\n\n"
                "Output:\n"
                "  1. ICP definition (one paragraph, concrete)\n"
                "  2. Positioning statement\n"
                "  3. Top 3 channels ranked by ICP fit\n"
                "  4. First-100-users tactical plan (week 1 actions)\n"
                "  5. Pricing hypothesis with reasoning"
            ),
            expected_output="GTM plan with concrete first-week actions.",
            agent=growth,
        ),
        Task(
            description=(
                f"Red-team the entire plan for: {idea}\n\n"
                "Review all outputs above. Produce a risk register:\n"
                "  1. Top 5 reasons this fails\n"
                "  2. The assumption each reason attacks\n"
                "  3. The cheapest test to falsify each assumption before investing\n\n"
                "Be brutal. Surface the risks no one else named."
            ),
            expected_output="Risk register, brutally honest.",
            agent=devil,
        ),
        Task(
            description=(
                f"Synthesize everything into a final executive brief for: {idea}\n\n"
                "Include:\n"
                "  1. One-paragraph thesis\n"
                "  2. MVP scope (3 bullets max)\n"
                "  3. Stack choice (one line)\n"
                "  4. GTM summary (3 bullets max)\n"
                "  5. Top 3 risks\n"
                "  6. A clear GO / NO-GO / PIVOT call with reasoning\n\n"
                "Max one page equivalent. Be decisive."
            ),
            expected_output="Executive brief, max one page, decisive.",
            agent=ceo,
        ),
    ]


@observe(name="thinktank_run")
def run_thinktank(idea: str) -> dict:
   crew = Crew(
       agents=[analyst, product, cto, growth, devil, ceo],
       tasks=build_tasks(idea),
       process=Process.sequential,
       verbose=True,
   )
   crew_output = crew.kickoff()

   # Map each agent's role → their raw task output
   agent_outputs = {}
   for task in crew_output.tasks_output:
       agent_outputs[task.agent] = task.raw

   return {
       "final": str(crew_output),
       "analyst": agent_outputs.get("Competitive Intelligence Analyst", ""),
       "product": agent_outputs.get("Product Strategist", ""),
       "cto": agent_outputs.get("CTO and Architect", ""),
       "growth": agent_outputs.get("Head of Growth", ""),
       "devil": agent_outputs.get("Devil's Advocate", ""),
       "ceo": agent_outputs.get("CEO and Orchestrator", ""),
   }
