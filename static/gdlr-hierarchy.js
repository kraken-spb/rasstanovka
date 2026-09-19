(() => {
  'use strict';
  window.gdlrHierarchy = {render(list, rows, nodes) {
    if (!nodes?.length) return;
    const cards = new Map(rows.map((r,i)=>[r.id,list.children[i]])), used=new Set();
    const E=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;};
    function children(parent, target) {
      for(const node of nodes.filter(n=>n.parent_id===parent)) {
        if(node.is_group) {
          const details=E('details','','gdlr-tree'), body=E('div','','gdlr-tree-body');details.open=parent===null;
          details.append(E('summary',node.label));children(node.id,body);details.append(body);target.append(details);
        } else {
          const row=rows.find(r=>r.id===node.category_id)||(!node.category_id&&rows.find(r=>r.name_key===node.name_key));
          if(row&&cards.get(row.id)) {target.append(cards.get(row.id));used.add(row.id);}
          else {
            const item=E('div','','gdlr-unlinked');item.append(E('span',node.label),E('small','Нет в справочнике'));
            const field=document.getElementById('category-create-name');
            if(field && !field.disabled && document.querySelector('.app-shell').dataset.role==='super_admin') {
              const button=E('button','Добавить','secondary-button');button.type='button';button.addEventListener('click',()=>{
                field.value=node.label;field.dispatchEvent(new Event('input',{bubbles:true}));field.scrollIntoView({block:'center'});field.focus();
              });item.append(button);
            }
            target.append(item);
          }
        }
      }
    }
    const content=document.createDocumentFragment();children(null,content);
    const remaining=rows.filter(r=>!used.has(r.id));
    if(remaining.length){const d=E('details','','gdlr-tree');d.open=true;d.append(E('summary','Вне иерархии отчёта'));for(const row of remaining)d.append(cards.get(row.id));content.append(d);}
    list.replaceChildren(content);
  }};
})();
