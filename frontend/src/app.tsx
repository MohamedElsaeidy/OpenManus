import { ConfirmDialog } from '@/components/block/confirm';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
} from '@/components/ui/sidebar';
import { Button } from '@/components/ui/button';
import { LoaderIcon, LogOut, MessageSquare, Plus, PowerOff, Settings, Shield, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { SidebarProvider } from './components/ui/sidebar';
import { useConversations } from './hooks/use-conversations';
import { cn } from './libs/utils';
import AuthPage from './pages/AuthPage';
import AdminPage from './pages/AdminPage';
import ConversationSettingsPage from './pages/ConversationSettingsPage';
import HomePage from './pages/HomePage';
import TaskDetailPage from './pages/TaskDetailPage';
import { getMe, logout, type User } from './services/auth';
import { deleteConversation } from './services/conversations';
import { ejectModel, listModels, type ModelOption } from './services/models';

function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState(localStorage.getItem('openmanus.selectedModel') || '');
  const [isEjectingModel, setIsEjectingModel] = useState(false);
  const { conversations, activeConversationId, createNewConversation, refreshConversations, removeConversation, setActiveConversationId } = useConversations();
  const location = useLocation();
  const navigate = useNavigate();

  const currentTaskId = location.pathname.startsWith('/tasks/') ? location.pathname.split('/').pop() : undefined;

  useEffect(() => {
    getMe().then(res => setUser(res.user || null));
  }, []);

  useEffect(() => {
    if (user) {
      refreshConversations();
      listModels().then(items => {
        setModels(items);
        if (!selectedModel && items[0]?.id) {
          setSelectedModel(items[0].id);
          localStorage.setItem('openmanus.selectedModel', items[0].id);
        }
      });
    }
  }, [refreshConversations, user]);

  if (user === undefined) {
    return <div className="flex h-screen w-screen items-center justify-center text-sm text-muted-foreground">Loading...</div>;
  }

  if (!user) {
    return <AuthPage onSignedIn={signedInUser => setUser(signedInUser)} />;
  }

  const handleNewConversation = async () => {
    const conversation = await createNewConversation();
    navigate(`/conversations/${conversation.id}`);
  };

  const handleLogout = async () => {
    await logout();
    setUser(null);
    setActiveConversationId(undefined);
  };

  const handleDeleteConversation = async (conversationId: string) => {
    await deleteConversation(conversationId);
    removeConversation(conversationId);
    if (location.pathname.includes(conversationId)) {
      navigate('/');
    }
  };

  const handleEjectModel = async () => {
    if (isEjectingModel) return;
    setIsEjectingModel(true);
    try {
      const res = await ejectModel(selectedModel || undefined);
      if (!res.ok) {
        toast.error(res.detail || 'Could not eject model');
        return;
      }
      toast.success(`Ejected model${res.instance_id ? `: ${res.instance_id}` : ''}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not eject model');
    } finally {
      setIsEjectingModel(false);
    }
  };

  return (
    <SidebarProvider>
      <div className="flex h-screen w-screen">
        <Sidebar>
          <SidebarHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold">OpenManus</span>
              </div>
              <Link to="https://github.com/FoundationAgents/OpenManus/tree/openmanus-v2" target="_blank" rel="noopener noreferrer">
                <svg viewBox="0 0 24 24" className="h-5 w-5 opacity-80" color="text-inherit" xmlns="http://www.w3.org/2000/svg">
                  <title>GitHub</title>
                  <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
                </svg>
              </Link>
            </div>
            <Button size="sm" className="mt-3 w-full" onClick={handleNewConversation}>
              <Plus className="size-4" />
              New conversation
            </Button>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Model</SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="mx-2 flex items-center gap-2">
                  <select
                    className="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm"
                    value={selectedModel}
                    onChange={event => {
                      setSelectedModel(event.target.value);
                      localStorage.setItem('openmanus.selectedModel', event.target.value);
                    }}
                  >
                    {models.map(model => (
                      <option key={model.id} value={model.id}>
                        {model.id}
                      </option>
                    ))}
                  </select>
                  <Button
                    size="icon"
                    variant="outline"
                    title="Eject model from LM Studio memory"
                    onClick={handleEjectModel}
                    disabled={isEjectingModel || !selectedModel}
                  >
                    {isEjectingModel ? <LoaderIcon className="size-4 animate-spin" /> : <PowerOff className="size-4" />}
                  </Button>
                </div>
              </SidebarGroupContent>
            </SidebarGroup>
            <SidebarGroup>
              <SidebarGroupLabel>Conversations</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {conversations.map(item => (
                    <SidebarMenuItem key={item.id}>
                      <div className="group flex items-center gap-1 rounded-md pr-1 hover:bg-muted">
                        <Link
                          to={`/conversations/${item.id}`}
                          onClick={() => setActiveConversationId(item.id)}
                          className={cn(
                            'flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-2 text-sm',
                            (activeConversationId === item.id || currentTaskId === item.latest_task_id) && 'bg-muted',
                          )}
                        >
                          <MessageSquare className="size-4" />
                          <span className="truncate">{item.title}</span>
                        </Link>
                        <Link
                          to={`/conversations/${item.id}/settings`}
                          className="rounded-md p-1.5 opacity-0 hover:bg-background group-hover:opacity-100"
                          title="Conversation settings"
                        >
                          <Settings className="size-4" />
                        </Link>
                        <button
                          className="rounded-md p-1.5 opacity-0 hover:bg-background group-hover:opacity-100"
                          title="Delete conversation"
                          onClick={() => handleDeleteConversation(item.id)}
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
          <SidebarFooter>
            <div className="space-y-2 p-2">
              <div className="min-w-0 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="truncate font-medium">{user.name}</div>
                  {user.role === 'admin' && (
                    <span className="rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-normal text-muted-foreground">
                      Admin
                    </span>
                  )}
                </div>
                <div className="truncate text-xs text-muted-foreground">{user.email}</div>
              </div>
              {user.role === 'admin' && (
                <Button variant="outline" size="sm" className="w-full" onClick={() => navigate('/admin')}>
                  <Shield className="size-4" />
                  Admin
                </Button>
              )}
              <Button variant="outline" size="sm" className="w-full" onClick={handleLogout}>
                <LogOut className="size-4" />
                Sign out
              </Button>
            </div>
          </SidebarFooter>
        </Sidebar>
        <main className="relative flex-1">
          <Routes>
            <Route path="/" element={<HomePage selectedModel={selectedModel} />} />
            <Route path="/conversations/:conversationId" element={<TaskDetailPage selectedModel={selectedModel} />} />
            <Route path="/conversations/:conversationId/settings" element={<ConversationSettingsPage />} />
            <Route path="/tasks/:taskId" element={<TaskDetailPage selectedModel={selectedModel} />} />
            <Route path="/admin" element={<AdminPage />} />
          </Routes>
        </main>
        <ConfirmDialog />
      </div>
    </SidebarProvider>
  );
}

export default App;
