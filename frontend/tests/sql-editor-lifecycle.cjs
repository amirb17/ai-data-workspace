// Real CodeMirror state under a lightweight DOM; live interaction is also checked in the browser.
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window()
 for(const name of ['window','document','HTMLElement','Element','Node','Text','Event','MouseEvent','KeyboardEvent','MutationObserver','DOMRect','Range','navigator'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.getComputedStyle=window.getComputedStyle.bind(window)
 global.requestAnimationFrame=window.requestAnimationFrame.bind(window);global.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{EditorView}=require('@codemirror/view'),{undo}=require('@codemirror/commands')
 const {SqlEditor}=require('../src/features/workspaces/sql/SqlEditor.tsx')
 const column={name:'id',type:'TEXT',role:'IDENTIFIER',queryable:true,description:''}
 const catalog={datasets:[{alias:'orders',name:'Orders',queryable:true,columns:[column]}],relationships:[],capabilities:{functions:['COUNT']}}
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 let text='SELECT * FROM ord'
 const render=async current=>act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(SqlEditor,{value:text,catalog:current,onChange:value=>{text=value},run:()=>{},save:()=>{},format:()=>{},disabled:false}))))
 await render(catalog)
 const editor=EditorView.findFromDOM(host.querySelector('.cm-editor'))
 assert.ok(editor)
 const {syntaxTree}=require('@codemirror/language')
 assert.equal(syntaxTree(editor.state).type.name,'Script','SQL language is configured after strict mount')
 await act(async()=>{editor.dispatch({selection:{anchor:text.length}});editor.dispatch({changes:{from:text.length,insert:'e'},selection:{anchor:text.length+1}})})
 const caret=editor.state.selection.main.head,edited=text
 await render({...catalog,relationships:[{relationship_id:1}]})
 assert.equal(EditorView.findFromDOM(host.querySelector('.cm-editor')),editor,'metadata refresh preserves editor instance')
 assert.equal(editor.state.selection.main.head,caret,'metadata refresh preserves caret')
 assert.equal(editor.state.doc.toString(),edited)
 await render({...catalog,datasets:[{...catalog.datasets[0],queryable:false}]})
 assert.equal(EditorView.findFromDOM(host.querySelector('.cm-editor')),editor,'eligibility change reconfigures rather than recreates editor')
 assert.equal(editor.state.selection.main.head,caret)
 assert.equal(syntaxTree(editor.state).type.name,'Script','SQL language survives completion reconfiguration')
 await act(async()=>assert.equal(undo(editor),true))
 assert.equal(editor.state.doc.toString(),'SELECT * FROM ord','undo survives catalog reconfiguration')
 await act(async()=>root.unmount());await window.happyDOM.close()
 console.log('Real CodeMirror lifecycle passed: strict mount, metadata refresh, eligibility change, caret/document and undo preservation')
}
main().catch(error=>{console.error(error);process.exit(1)})
