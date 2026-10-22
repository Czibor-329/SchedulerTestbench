/** 回放注释专用确定性 DOM 夹具，仅提供节点身份、注释生命周期和文字边界，不模拟布局引擎。 */
"use strict";

/** 提供只读矩形；等待和下一站注释不参与设备定位。 */
class ReplayAnnotationElement {
  constructor(ownerDocument, className = "", dataset = {}, rectangle = {left:0,top:0,right:0,bottom:0}) {
    this.ownerDocument = ownerDocument;
    this.className = className;
    this.dataset = {...dataset};
    this.rectangle = {...rectangle};
    this.children = [];
    this.parentElement = null;
    this.title = "";
    this.textContent = "";
    this.style = {values:{},setProperty(name,value){this.values[name]=value;},getPropertyValue(name){return this.values[name] || "";}};
    this.classList = {
      contains: name=>this.className.split(/\s+/).includes(name),
      toggle: (name,enabled)=>{
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) values.add(name); else values.delete(name);
        this.className = [...values].join(" ");
      },
    };
  }
  /** 附加独立注释节点，不改变硬件坐标。 */
  appendChild(child) { this.children.push(child); child.parentElement=this; return child; }
  /** 从当前父节点移除注释。 */
  remove() { if(this.parentElement) this.parentElement.children=this.parentElement.children.filter(child=>child!==this); this.parentElement=null; }
  /** 匹配测试涉及的class和data属性，不通过生产HTML扫描断言偶然实现。 */
  matches(selector) {
    const classes=[...selector.matchAll(/\.([\w-]+)/g)].map(match=>match[1]);
    const attributes=[...selector.matchAll(/\[data-([\w-]+)\]/g)].map(match=>match[1].replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase()));
    return classes.every(name=>this.classList.contains(name)) && attributes.every(name=>this.dataset[name]!==undefined);
  }
  /** 返回全部后代中匹配单class或逗号组合的节点。 */
  querySelectorAll(selector) {
    const alternatives=selector.split(",").map(value=>value.trim());
    const nodes=[];
    const visit=node=>{ for(const child of node.children){ if(alternatives.some(value=>child.matches(value))) nodes.push(child); visit(child); } };
    visit(this); return nodes;
  }
  /** 返回首个匹配节点。 */
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  /** 删除注释属性，设备身份继续保留。 */
  removeAttribute(name) { if(name==="title") this.title=""; }
  /** 返回确定的边界，不模拟浏览器布局。 */
  getBoundingClientRect() {
    return {left:this.rectangle.left,right:this.rectangle.right,top:this.rectangle.top,bottom:this.rectangle.bottom,
      width:this.rectangle.right-this.rectangle.left,height:this.rectangle.bottom-this.rectangle.top};
  }
}

/** 构造单模块、一片晶圆的注释区域；画布宽度为零时显式跳过像素避让。 */
function buildReplayAnnotationStage(moduleName="PM1", cooler=false) {
  const ownerDocument={createElement(){return new ReplayAnnotationElement(ownerDocument);}};
  const stage=new ReplayAnnotationElement(ownerDocument);
  const canvas=stage.appendChild(new ReplayAnnotationElement(ownerDocument,"reference-grid-canvas"));
  const wrapper=canvas.appendChild(new ReplayAnnotationElement(ownerDocument,"reference-module-position",{replayName:moduleName}));
  const name=wrapper.appendChild(new ReplayAnnotationElement(ownerDocument,"equipment-external-name")); name.textContent=moduleName;
  const body=wrapper.appendChild(new ReplayAnnotationElement(ownerDocument,cooler?"equipment-utility equipment-cooler-top-view":"equipment-card"));
  const wafer=body.appendChild(new ReplayAnnotationElement(ownerDocument,"wafer-token",{replayWafer:"1"}));
  const surface=wafer.appendChild(new ReplayAnnotationElement(ownerDocument));
  const origin=surface.appendChild(new ReplayAnnotationElement(ownerDocument,"wafer-origin-label")); origin.textContent="LP1.1";
  return {stage,canvas,wrapper,name,body,wafer,surface,origin,ownerDocument};
}

module.exports={ReplayAnnotationElement,buildReplayAnnotationStage};
