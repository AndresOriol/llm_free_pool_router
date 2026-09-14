This document is used to annotate ideas of improvement that can be later used by tha coding agent.

## Improving the router

I feel that it would be better to build a more robust router that would led by its configuration to select a model or some models, so you can have more control over what you are using.
Improve the rerouting so that there are less misses when there is a lot of traffic for a model.

### Optional (high complexity)

Create an automatic classifier that selects the best model depending on the task and current availability of the pool. This implementation only makes sense if the previous 2 themes are already implemented. This improvement by itself can be considered an independent project.