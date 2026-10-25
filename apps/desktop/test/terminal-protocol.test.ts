import{it,expect}from"vitest";import{randomUUID}from"node:crypto";import{terminalRequest,terminalResult}from"../src/terminal-protocol.js";import{parsePayload}from"../src/contract.js";
it("requires operation-specific IDs and bounded input/geometry; no arbitrary executable or environment",()=>{
 const requestId=randomUUID(),sessionId=randomUUID();
 expect(terminalRequest.safeParse({operation:"read",requestId,sessionId,offset:0}).success).toBe(true);
 for(const fields of [{operation:"input",input:"x"},{operation:"resize",sessionId,cols:0,rows:5},{operation:"agentRead",threadId:"t"},{operation:"start",cwd:"/outside",command:"evil"},{operation:"input",sessionId,input:"x".repeat(4097)}])expect(terminalRequest.safeParse({requestId,...fields}).success).toBe(false);
 expect(parsePayload({type:"desktop/terminal",requestId,operation:"list"}).type).toBe("desktop/terminal");
 expect(terminalResult.safeParse({data:"A".repeat(90001)}).success).toBe(false);
});
