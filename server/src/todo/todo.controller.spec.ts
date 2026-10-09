import { Test, TestingModule } from '@nestjs/testing';
import { TodoController } from './todo.controller';
import { TodoService } from './todo.service';

describe('TodoController', () => {
  let controller: TodoController;
  const service = {
    getAllTodos: jest.fn(),
    createTodo: jest.fn(),
    updateTodo: jest.fn(),
    deleteTodo: jest.fn(),
  };
  // L'identifiant vient toujours du jeton verifie (req.user), jamais du corps de la requete
  const req = { user: { id: 'alice', email: 'alice@test.fr' } };

  beforeEach(async () => {
    jest.resetAllMocks();
    const module: TestingModule = await Test.createTestingModule({
      controllers: [TodoController],
      providers: [{ provide: TodoService, useValue: service }],
    }).compile();
    controller = module.get<TodoController>(TodoController);
  });

  it('liste les taches de l utilisateur authentifie', () => {
    controller.getAllTodos(req);
    expect(service.getAllTodos).toHaveBeenCalledWith('alice');
  });

  it('cree la tache pour l utilisateur authentifie', () => {
    controller.createTodo(req, 'acheter du pain');
    expect(service.createTodo).toHaveBeenCalledWith('alice', 'acheter du pain');
  });

  it('modifie et supprime au nom de l utilisateur authentifie', () => {
    controller.updateTodo('t1', req, { completed: true });
    controller.deleteTodo(req, 't1');
    expect(service.updateTodo).toHaveBeenCalledWith('alice', 't1', { completed: true });
    expect(service.deleteTodo).toHaveBeenCalledWith('alice', 't1');
  });
});
