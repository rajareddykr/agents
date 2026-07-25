AGT SDK — install guide

This zip contains:

  wheels/agt_os_kernel-<version>-py3-none-any.whl   the engine
  wheels/agt_sdk-<version>-py3-none-any.whl         the SDK itself


QUICK INSTALL (LangChain example)
  unzip aisec-agents-gov-sdk.zip -d ~/agt-sdk
  pip install --find-links=~/agt-sdk/wheels "agt-sdk[langchain]"
  python -c "from agt_sdk import governed; print('sdk ready')"


Framework extras:
  langgraph       for LangGraph agents
  langchain       for LangChain agents
  crewai          for CrewAI agents
  autogen         for AutoGen agents
  openai-agents   for the OpenAI Agents SDK
  (omit)          for plain Python (no framework)


NEXT STEPS
  See AGT-Getting-Started for the five-step walkthrough
  (install -> configure -> first agent -> run -> dashboard).
