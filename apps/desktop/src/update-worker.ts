import {readFile,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {installPlan,performSwap,waitForParentExit} from "./update-install.js";
async function main(){const [file,digest]=process.argv.slice(2);if(!file||!digest)throw Error('Missing update transaction.');const bytes=await readFile(file);if(createHash('sha256').update(bytes).digest('hex')!==digest)throw Error('Update transaction changed.');const plan=installPlan.parse(JSON.parse(bytes.toString('utf8')));
 try{await waitForParentExit(plan.parentPid,Date.now()+60000);
 await performSwap(plan,async(app,profile)=>{await promisify(execFile)('/usr/bin/open',['-n','--env',`CRUCIBLE_DESKTOP_PROFILE=${profile}`,app],{env:{PATH:'/usr/bin:/bin:/usr/sbin:/sbin'},timeout:30000,maxBuffer:65536});});}
 catch{await writeFile(plan.result,JSON.stringify({version:1,status:'failed',app:plan.current,backup:plan.backup,message:'Update could not be installed. The previous application is retained.'}),{mode:0o600});throw Error('Update installation failed.');}
}
void main().catch(()=>{process.exitCode=1;});
