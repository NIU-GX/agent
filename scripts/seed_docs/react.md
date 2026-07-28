# ReAct Agent

ReAct（Reason + Act）是工具型 Agent 的常见范式：模型交替进行推理（Thought）与行动（Action），
并根据观察（Observation）继续下一步，直到给出最终答案。

企业落地时必须设置 max_iterations，并检测重复工具调用，避免无限循环。
