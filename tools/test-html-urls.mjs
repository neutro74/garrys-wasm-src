import assert from 'node:assert/strict';
import {resolveResourceUrls} from '../web/html-panels.js';
const html='<script src="js/menu/menuApp.js"></script><link href="css/menu/Menu.css"><a href="#/newgame">Start</a>';
const result=resolveResourceUrls(html,'asset://garrysmod/html/menu.html');
assert.match(result,/src="\/asset\/garrysmod\/html\/js\/menu\/menuApp.js"/);assert.match(result,/href="\/asset\/garrysmod\/html\/css\/menu\/Menu.css"/);assert.match(result,/href="#\/newgame"/);
console.log('panel asset resource URL resolution: PASS');
