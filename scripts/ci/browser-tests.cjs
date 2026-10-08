// Keep each fixture's screenshots separate; no production endpoints are used.
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const suites=['test_boot_loading','test_tutorial_ui','test_explore_ui','test_welcome_ui','test_plan_change_ui','test_admin_layout','test_mobile_layout','visual_smoke'];
for(const suite of suites){
 const result=spawnSync(process.execPath,[`tests/${suite}.cjs`],{stdio:'inherit',env:{...process.env,VISUAL_OUTPUT:path.resolve('artifacts/ui',suite)}});
 if(result.status!==0)process.exit(result.status||1);
}
