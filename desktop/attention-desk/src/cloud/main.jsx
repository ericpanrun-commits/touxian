import React from 'react';
import {createRoot} from 'react-dom/client';
import CloudApp from './CloudApp';
import '../styles.css';
import '../personal/personal.css';
import '../personal/desk.css';
import './cloud.css';
import './responsive.css';
import {registerWorker} from './register-worker';

class RecoveryBoundary extends React.Component {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<main className="desk-shell cloud-auth"><section className="personal-section"><h1>Eric OS 暂时没有打开</h1><p>已保存的内容仍然保留，可以重新打开继续。</p><button className="button button-primary" type="button" onClick={()=>location.reload()}>重新打开</button></section></main>:this.props.children;}
}
createRoot(document.getElementById('root')).render(<React.StrictMode><RecoveryBoundary><CloudApp/></RecoveryBoundary></React.StrictMode>);
if(import.meta.env.PROD)registerWorker();
