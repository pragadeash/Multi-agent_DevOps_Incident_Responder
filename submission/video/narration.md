0. This store support agent talks through Amazon Bedrock. For this demo it is told to act without asking, and Strands Decider 2B checks every refund or cancellation before it runs.

1. A store support agent that can refund and cancel orders, and cannot do it to the wrong order. It can look up, refund and cancel orders in chat; check every irreversible action with Strands Decider 2B before it runs; send unsure cases to a human instead of guessing; run on Amazon Bedrock AgentCore Runtime.

2. The agent you are about to see is running on Amazon Bedrock AgentCore Runtime.

3. The customer wants a refund but never says which order. The decider scored args grounded 0.44, explicit request 0.79, premature 0.23, so the verdict was guide, in 3.1 seconds.

4. Now the customer gives the order id and asks for the refund explicitly. The decider scored args grounded 0.61, explicit request 0.89, premature 0.13, so the verdict was proceed, in 3.9 seconds.

5. This one is a question, not a request to act. No decision was needed on this turn.

6. On 24 labelled decisions, with the decider running locally on this machine, Strands Decider was 79% accurate at a median of 295 milliseconds, against 96% and 1276 milliseconds for the Bedrock LLM asked the same questions.

7. One forward pass per decision, typed answers with a confidence the code can branch on, and Amazon Bedrock for the conversation.