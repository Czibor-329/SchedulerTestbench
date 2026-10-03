/** 回放来源切换测试的最小 DOM；只记录用户可见字段，不解析设备 HTML 或模拟业务状态。 */
"use strict";

/** 提供工作台依赖的页面节点接口，让来源竞态测试与浏览布局测试分别覆盖自己的边界。 */
function replayWorkspaceDocument() {
  const elements = new Map();
  const root = {activeElement:null, getElementById(id) {
    if (!elements.has(id)) {
      const attributes = new Map();
      const classes = new Set();
      elements.set(id, {ownerDocument:root,dataset:{},hidden:false,disabled:false,checked:false,
        value:"",min:"",max:"",step:"",innerHTML:"",textContent:"",href:"",listeners:new Map(),
        style:{setProperty(){}},classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},toggle(name,value){if(value) classes.add(name);else classes.delete(name);},contains(name){return classes.has(name);}},
        addEventListener(type,handler){this.listeners.set(type,handler);},
        setAttribute(name,value){attributes.set(name,String(value));},getAttribute(name){return attributes.get(name)??null;},
        querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;},contains(){return false;},focus(){},click(){}});
    }
    return elements.get(id);
  },addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];} };
  return root;
}
module.exports={replayWorkspaceDocument};
