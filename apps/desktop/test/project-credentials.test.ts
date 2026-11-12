import{afterEach,expect,it}from"vitest";import{mkdtempSync,writeFileSync,readFileSync,rmSync}from"node:fs";import{tmpdir}from"node:os";import{join}from"node:path";import{projectSpawnEnv,importLegacyCredentials}from"../src/project-credentials.js";
const dirs:string[]=[];afterEach(()=>dirs.splice(0).forEach(d=>rmSync(d,{recursive:true,force:true})));
it("desktop backend sees only selected project credentials; watchers inherit none; OS process controls remain",()=>{
 const ambient={PATH:"/bin",OPENAI_API_KEY:"foreign",ANTHROPIC_API_KEY:"foreign2",WATSONX_URL:"foreign-url",CRUCIBLE_CHATGPT_REGISTRATION:"foreign-id",CRUCIBLE_OWNER_KEY:"capability"};
 expect(projectSpawnEnv(ambient)).toEqual({PATH:"/bin",CRUCIBLE_OWNER_KEY:"capability"});expect(projectSpawnEnv(ambient,{backend:"openai",model:"m",apiKey:{envVar:"OPENAI_API_KEY",value:"project-a"}})).toEqual({PATH:"/bin",CRUCIBLE_OWNER_KEY:"capability",OPENAI_API_KEY:"project-a"});expect(projectSpawnEnv(ambient,{backend:"chatgpt",model:""})).not.toHaveProperty("CRUCIBLE_CHATGPT_REGISTRATION");
});
it("explicit import preserves old ciphertext and refuses overwriting any project's existing keys",()=>{
 const dir=mkdtempSync(join(tmpdir(),"cp9-credentials-"));dirs.push(dir);const source=join(dir,"old.json"),target=join(dir,"project","secrets.json");writeFileSync(source,JSON.stringify({"crucible.providerKey.openai":"YWJj","unrecognized":"omit"}));const original=readFileSync(source);expect(importLegacyCredentials(source,target)).toMatch(/^[a-f0-9]{64}$/);expect(JSON.parse(readFileSync(target,"utf8"))).toEqual({"crucible.providerKey.openai":"YWJj"});expect(readFileSync(source)).toEqual(original);expect(()=>importLegacyCredentials(source,target)).toThrow("already");
});
it("MCP secrets reach only the backend, never a watcher or the ambient environment's copy",()=>{
 const ambient={PATH:"/bin",CRUCIBLE_MCP_SECRETS:JSON.stringify({FOREIGN:"x"})};
 expect(projectSpawnEnv(ambient)).toEqual({PATH:"/bin"});
 expect(projectSpawnEnv(ambient,{backend:"openai",model:"m"})).toEqual({PATH:"/bin"});
 const backend=projectSpawnEnv(ambient,{backend:"openai",model:"m",mcpSecrets:{GITHUB_PAT:"tok"}});
 expect(JSON.parse(backend.CRUCIBLE_MCP_SECRETS!)).toEqual({GITHUB_PAT:"tok"});
});
