import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { HttpStatus } from '@nestjs/common';
import { TodoService } from './todo.service';
import { TodoEntity } from './todo.entity';
import { UserEntity } from '../user/user.entity';

const user = (id: string) => {
  const u = new UserEntity();
  u.id = id;
  u.email = `${id}@test.fr`;
  u.password = 'hache';
  u.createdOn = new Date('2026-01-01');
  return u;
};

const todo = (id: string, author: UserEntity) =>
  Object.assign(new TodoEntity(), { id, content: 'tache', completed: false, author });

describe('TodoService', () => {
  let service: TodoService;
  let todos: Record<string, jest.Mock>;
  let users: Record<string, jest.Mock>;

  beforeAll(() => {
    process.env.SECRET = 'secret-de-test';
  });

  beforeEach(async () => {
    todos = {
      find: jest.fn(),
      findOne: jest.fn(),
      create: jest.fn(),
      save: jest.fn(),
      update: jest.fn(),
      remove: jest.fn(),
    };
    users = { findOne: jest.fn() };
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        TodoService,
        { provide: getRepositoryToken(TodoEntity), useValue: todos },
        { provide: getRepositoryToken(UserEntity), useValue: users },
      ],
    }).compile();
    service = module.get<TodoService>(TodoService);
  });

  it('creation : la tache appartient a l utilisateur connecte', async () => {
    const alice = user('alice');
    users.findOne.mockResolvedValue(alice);
    todos.create.mockImplementation(data => Object.assign(new TodoEntity(), data));
    const result = await service.createTodo('alice', 'acheter du pain');
    expect(todos.create).toHaveBeenCalledWith({ content: 'acheter du pain', author: alice });
    expect(result.author).toEqual({ id: 'alice', createdOn: alice.createdOn, email: 'alice@test.fr' });
  });

  it.each([undefined, null, '', '   '])(
    'creation : refuse une tache sans contenu (%p)',
    async content => {
      await expect(service.createTodo('alice', content as any)).rejects.toMatchObject({
        status: HttpStatus.BAD_REQUEST,
      });
      expect(todos.save).not.toHaveBeenCalled();
    },
  );

  it('les donnees renvoyees ne contiennent jamais le mot de passe de l auteur', async () => {
    const alice = user('alice');
    users.findOne.mockResolvedValue(alice);
    todos.find.mockResolvedValue([todo('t1', alice)]);
    const result = await service.getAllTodos('alice');
    expect(result[0].author).not.toHaveProperty('password');
  });

  it('modification : refuse de modifier la tache d un autre utilisateur', async () => {
    todos.findOne.mockResolvedValue(todo('t1', user('alice')));
    await expect(
      service.updateTodo('bob', 't1', { completed: true }),
    ).rejects.toMatchObject({ status: HttpStatus.UNAUTHORIZED });
    expect(todos.update).not.toHaveBeenCalled();
  });

  it('suppression : refuse de supprimer la tache d un autre utilisateur', async () => {
    todos.findOne.mockResolvedValue(todo('t1', user('alice')));
    await expect(service.deleteTodo('bob', 't1')).rejects.toMatchObject({
      status: HttpStatus.UNAUTHORIZED,
    });
    expect(todos.remove).not.toHaveBeenCalled();
  });

  it('modification : 404 si la tache n existe pas', async () => {
    todos.findOne.mockResolvedValue(undefined);
    await expect(
      service.updateTodo('alice', 'absente', { completed: true }),
    ).rejects.toMatchObject({ status: HttpStatus.NOT_FOUND });
  });

  it('modification : le proprietaire peut terminer sa tache', async () => {
    todos.findOne.mockResolvedValue(todo('t1', user('alice')));
    await service.updateTodo('alice', 't1', { completed: true });
    expect(todos.update).toHaveBeenCalledWith({ id: 't1' }, { completed: true });
  });
});
