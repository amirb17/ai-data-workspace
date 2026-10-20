import {lazy, Suspense} from 'react'
import {Route, Routes} from 'react-router-dom'
import {PublicPage} from './pages/Public/PublicPage'
import {AuthPage} from './pages/Auth/AuthPage'
import {LoginPage} from './pages/Auth/LoginPage'
import {SignupPage} from './pages/Auth/SignupPage'
import {NotFoundPage} from './components/ui/NotFoundPage'
const AppLayout=lazy(()=>import('./AppLayout'))
export default function App(){
 return <Suspense fallback={<p role="status" className="p-8">Loading DataRise…</p>}><Routes>
 <Route path="/" element={<PublicPage kind="home"/>}/>
 <Route path="/how-it-works" element={<PublicPage kind="how"/>}/>
 <Route path="/about" element={<PublicPage kind="about"/>}/>
 <Route path="/login" element={<LoginPage/>}/><Route path="/signup" element={<SignupPage/>}/>
 {['forgot-password','verify','reset-password'].map(page=><Route key={page} path={'/'+page} element={<AuthPage key={page} mode={page}/>}/>)}
 <Route path="/app/*" element={<AppLayout/>}/><Route path="*" element={<NotFoundPage/>}/>
 </Routes></Suspense>
}
