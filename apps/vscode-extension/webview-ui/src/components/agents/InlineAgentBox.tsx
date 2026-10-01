export function InlineAgentBox({ agentId }: { agentId: string }) {
  return <div data-testid={`agent-box-${agentId}`} />;
}
